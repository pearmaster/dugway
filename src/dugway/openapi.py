"""OpenAPI support: a service for an API described by an OpenAPI document, and a step that calls
one of its operations and checks the response against the document.
"""

import json
from copy import copy
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
from jacobsjsonschema import draft4, draft2020_12
from jacobsjsonschema.draft4 import JsonSchemaValidationError

from .api_spec import load_api_document
from .capabilities import (
    JsonSchemaExpectation,
    RawContentCapability,
    ServiceDependency,
    ValueCapability,
)
from .expectations import ExpectationFailure, FailedTestStep
from .meta import JsonConfigType, JsonContentType, JsonSchemaType
from .runner import DugwayRunner
from .service import Service
from .step import TestStep
from .web import HttpService

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class OpenApi30SchemaValidator(draft4.Validator):
    """Validates against an OpenAPI 3.0 Schema Object, which is JSON Schema draft 4 with 'nullable'."""

    def _validate(self, data: JsonContentType, schema: Any) -> bool:
        if hasattr(schema, "_reference"):
            schema = schema.resolve()
        if data is None and isinstance(schema, dict) and schema.get("nullable", False) is True:
            return True
        return super()._validate(data, schema)


def schema_validator(openapi_version: str, schema: JsonSchemaType):
    """The right validator for a schema from a document of the given OpenAPI version.

    OpenAPI 3.1 schemas are JSON Schema 2020-12. Earlier 3.x documents use their own dialect.
    """
    if openapi_version.startswith("3.1"):
        return draft2020_12.Validator(schema)
    return OpenApi30SchemaValidator(schema)


def is_json_media_type(media_type: str) -> bool:
    """Whether a media type such as application/json or application/problem+json carries JSON."""
    media_type = media_type.split(";")[0].strip().lower()
    subtype = media_type.partition("/")[2]
    return subtype == "json" or subtype.endswith("+json")


def media_type_matches(documented: str, actual: str) -> bool:
    """Whether a response's media type is covered by a documented one, which may use wildcards."""
    documented, actual = documented.lower(), actual.lower()
    if documented == actual or documented == "*/*":
        return True
    doc_type, _, doc_subtype = documented.partition("/")
    act_type, _, _ = actual.partition("/")
    return doc_subtype == "*" and doc_type == act_type


def serialize_parameter(value: Any) -> str:
    """Serializes a parameter value for a path, header or cookie, in OpenAPI's simple style."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(serialize_parameter(v) for v in value)
    if isinstance(value, dict):
        return ",".join(f"{k},{serialize_parameter(v)}" for k, v in value.items())
    return str(value)


def query_pairs(name: str, value: Any) -> list[tuple[str, str]]:
    """Serializes a query parameter in OpenAPI's form style: arrays repeat the name, and objects
    give a parameter per property.
    """
    if isinstance(value, (list, tuple)):
        return [(name, serialize_parameter(v)) for v in value]
    if isinstance(value, dict):
        return [(k, serialize_parameter(v)) for k, v in value.items()]
    return [(name, serialize_parameter(value))]


class OpenApiService(Service):
    """An HTTP API described by an OpenAPI 3 document, which openapi_request steps call by operationId.

    The document is loaded along with the suite, with its $refs resolved. Requests are sent to
    baseUrl when it is given, and otherwise to the first server the document lists.
    """

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(runner, config)
        self._headers = {k: self._runner.template_eval(v) for (k, v) in config.get("headers", {}).items()}
        self._spec = None
        # The schema for the whole suite is built from an instance without any config
        if spec_path := config.get("spec"):
            self._spec = load_api_document(
                self._runner, self._runner.template_eval(spec_path), "OpenAPI", "openapi", ("3.",)
            )

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "spec": {
                    "type": "string",
                    "description": "Path to the OpenAPI 3 document, as YAML or JSON, relative to the suite file. "
                    "Templates are evaluated.",
                },
                "baseUrl": {
                    "type": "string",
                    "description": "Where requests are sent, such as http://localhost:8080/v1. Templates are "
                    "evaluated. Defaults to the first server the document lists.",
                },
                "headers": {
                    **HttpService.get_headers_schema(),
                    "description": "Headers sent with every request. Values are templates.",
                },
            },
            "required": ["spec"],
        }

    @property
    def spec(self):
        return self._spec

    @property
    def openapi_version(self) -> str:
        return str(self._spec.get("openapi", "3.0"))

    def find_operation(self, operation_id: str) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        """Finds an operation by its operationId, giving its path template, method, the operation
        itself, and the path item it belongs to.
        """
        for path, path_item in self._spec.get("paths", {}).items():
            for method in HTTP_METHODS:
                operation = path_item.get(method)
                if operation is not None and operation.get("operationId") == operation_id:
                    return path, method.upper(), operation, path_item
        raise FailedTestStep(f"The OpenAPI document has no operation with operationId '{operation_id}'")

    def base_url(self, operation: dict[str, Any], path_item: dict[str, Any]) -> str:
        if configured := self._config.get("baseUrl"):
            return self._runner.template_eval(configured).rstrip("/")
        # A server list on the operation or path overrides the document's
        servers = operation.get("servers") or path_item.get("servers") or self._spec.get("servers") or []
        if not servers:
            raise FailedTestStep("The OpenAPI document lists no servers, so the service needs a baseUrl")
        server = servers[0]
        url = server.get("url", "")
        for name, variable in server.get("variables", {}).items():
            url = url.replace(f"{{{name}}}", str(variable.get("default", "")))
        if not urlsplit(url).scheme:
            raise FailedTestStep(
                f"The OpenAPI document's server URL '{url}' is relative, so the service needs a baseUrl"
            )
        return url.rstrip("/")

    def make_request(self, method: str, url: str, **httpx_kwargs) -> httpx.Response:
        all_headers = copy(self._headers)
        all_headers.update(httpx_kwargs.get("headers", {}))
        httpx_kwargs["headers"] = all_headers
        return httpx.request(method, url, **httpx_kwargs)


class OpenApiRequest(TestStep):
    """Calls an operation of an openapi service by its operationId, and checks the response against
    the OpenAPI document.

    Parameters are given by name and placed wherever the document says: in the path, the query
    string, a header or a cookie. The step fails before sending anything if a parameter isn't
    documented, a required parameter is missing, or a required request body isn't given. A request
    body must have a documented content type, and a JSON body must match the documented schema.

    The response must have a documented status code and content type, carry every required
    response header, and have a body that matches the documented schema. Only after those checks
    pass are the step's own expectations checked. A JSON response body is available to later steps,
    such as jsonpath or save, as a value, and any body as it was received, for a deserialize step.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        self.serv_dep = ServiceDependency(runner, config)
        self._raw = RawContentCapability(runner, config)
        self._value = ValueCapability(runner, config)
        self._js_expect = JsonSchemaExpectation(runner, config)
        super().__init__(runner, config, [self.serv_dep, self._raw, self._value, self._js_expect])
        self._expectations = config.get("expect", {})

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "operationId": {
                    "type": "string",
                    "description": "The operationId of the operation to call, as given in the OpenAPI document.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Values for the operation's parameters, by name. Each is sent where the "
                    "document places it: in the path, query string, a header or a cookie. Arrays in the "
                    "query string repeat the name. Templates in string values are evaluated.",
                },
                "json": {
                    "description": "Request body, sent as JSON. Templates in string values are evaluated. "
                    "The Content-Type is the first JSON media type the document lists for the request body, "
                    "unless contentType is given.",
                },
                "content": {
                    "type": "string",
                    "description": "Request body, sent as text. Ignored when json is given. Templates are "
                    "evaluated. The Content-Type is the first media type the document lists for the request "
                    "body, unless contentType is given.",
                },
                "contentType": {
                    "type": "string",
                    "description": "Content-Type header for the request body, when the document's first "
                    "media type isn't the one wanted.",
                },
                "headers": {
                    **HttpService.get_headers_schema(),
                    "description": "Extra headers for this request, added to the service's headers. "
                    "Values are templates.",
                },
                "follow_redirects": {
                    "type": "boolean",
                    "default": True,
                    "description": "Follow redirect responses. The final response is the one checked.",
                },
                "expect": {
                    "type": "object",
                    "description": "Checks made on the response, after it has been checked against the "
                    "OpenAPI document.",
                    "properties": {
                        "status_code": {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 599,
                            "description": "Fail unless the response has this status code.",
                        }
                    },
                },
            },
            "required": ["operationId"],
        }

    @staticmethod
    def _documented_parameters(operation: dict[str, Any], path_item: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """The operation's parameters by name, with the operation's own overriding the path's."""
        documented = {}
        for parameter in [*path_item.get("parameters", []), *operation.get("parameters", [])]:
            documented[parameter["name"]] = parameter
        return documented

    def _place_parameters(self, operation_id: str, operation: dict[str, Any], path_item: dict[str, Any]):
        """Sorts the given parameter values into where each one is sent."""
        documented = self._documented_parameters(operation, path_item)
        given = self._runner.template_eval_all(dict(self._config.get("parameters", {})))
        if unknown := sorted(set(given) - set(documented)):
            raise FailedTestStep(
                f"Operation '{operation_id}' has no parameter named {', '.join(repr(n) for n in unknown)}. "
                f"It accepts: {', '.join(sorted(documented)) or 'none'}"
            )
        missing = sorted(name for name, p in documented.items() if p.get("required", False) and name not in given)
        if missing:
            raise FailedTestStep(f"Operation '{operation_id}' requires the parameter(s): {', '.join(missing)}")
        placed: dict[str, Any] = {"path": {}, "query": [], "header": {}, "cookie": {}}
        for name, value in given.items():
            location = documented[name].get("in")
            if location == "query":
                placed["query"].extend(query_pairs(name, value))
            elif location in placed:
                placed[location][name] = serialize_parameter(value)
            else:
                raise FailedTestStep(
                    f"Parameter '{name}' of operation '{operation_id}' has an unknown 'in': {location}"
                )
        return placed

    def _request_body(
        self, service: OpenApiService, operation_id: str, operation: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """The httpx arguments for the request body, and its Content-Type, after checking the body
        against the OpenAPI document.
        """
        request_body = operation.get("requestBody") or {}
        documented = request_body.get("content", {})
        media_types = list(documented.keys())
        # Checked by key because falsy bodies like {}, [], 0 and false are still bodies
        if "json" in self._config:
            # Prefer plain application/json, then any other JSON media type, over the rest
            json_types = [m for m in media_types if is_json_media_type(m)]
            preferred = [*(m for m in json_types if m.lower() == "application/json"), *json_types, *media_types]
            content_type = self._config.get("contentType", preferred[0] if preferred else "application/json")
            body = self._runner.template_eval_all(self._config["json"])
            kwargs = {"content": json.dumps(body).encode()}
        elif "content" in self._config:
            content_type = self._config.get("contentType", media_types[0] if media_types else None)
            body = self._runner.template_eval_all(self._config["content"])
            kwargs = {"content": body}
        else:
            if request_body.get("required", False):
                raise FailedTestStep(f"Operation '{operation_id}' requires a request body, given as json or content")
            return {}, None

        if not operation.get("requestBody"):
            raise FailedTestStep(f"Operation '{operation_id}' does not take a request body")
        bare_type = (content_type or "").split(";")[0].strip()
        matched = next((m for m in media_types if bare_type and media_type_matches(m, bare_type)), None)
        if media_types and matched is None:
            raise FailedTestStep(
                f"Request content type '{bare_type or '(none)'}' is not documented for operation "
                f"'{operation_id}'. It accepts: {', '.join(media_types)}"
            )
        schema = documented.get(matched, {}).get("schema") if matched else None
        if schema is not None and is_json_media_type(bare_type):
            self._validate_request_json(service, operation_id, schema, body, "json" in self._config)
        return kwargs, content_type

    @staticmethod
    def _validate_request_json(service: OpenApiService, operation_id: str, schema, body: Any, is_json: bool):
        """Checks a JSON request body against its documented schema. A body given as content is
        parsed first.
        """
        if not is_json:
            try:
                body = json.loads(body)
            except ValueError as e:
                raise FailedTestStep(
                    f"Request body for operation '{operation_id}' is not valid JSON, "
                    f"although its content type is JSON: {e}"
                ) from e
        try:
            schema_validator(service.openapi_version, schema).validate(body)
        except JsonSchemaValidationError as e:
            raise FailedTestStep(
                f"Request body does not match the OpenAPI schema for operation '{operation_id}': {e}"
            ) from e

    def run(self):
        service = self.serv_dep.get_service()
        operation_id = self._runner.template_eval(self._config["operationId"])
        path_template, method, operation, path_item = service.find_operation(operation_id)
        placed = self._place_parameters(operation_id, operation, path_item)
        body_kwargs, content_type = self._request_body(service, operation_id, operation)

        path = path_template
        for name, value in placed["path"].items():
            path = path.replace(f"{{{name}}}", quote(value, safe=""))
        url = service.base_url(operation, path_item) + path
        headers = {**placed["header"], **self._runner.template_eval_all(dict(self._config.get("headers", {})))}
        if content_type and "content-type" not in [h.lower() for h in headers]:
            headers["Content-Type"] = content_type

        shown_url = f"{url}?{urlencode(placed['query'])}" if placed["query"] else url
        self._runner._reporter.step_info(f"{method} Request ({operation_id})", shown_url)
        response = service.make_request(
            method,
            url,
            params=placed["query"],
            headers=headers,
            cookies=placed["cookie"],
            follow_redirects=self._config.get("follow_redirects", True),
            **body_kwargs,
        )
        self._runner._reporter.step_info(f"{response.status_code} Response", response.text)

        # The document's checks come first, and the step's own expectations after
        json_body = self._validate_response(service, operation_id, operation, response)
        self._raw.set_content(response.content, {"contentType": response.headers.get("content-type")})
        if json_body is not _NOT_JSON:
            self._value.set(json_body)
        self._check_expectations(response, json_body)

    def _validate_response(self, service, operation_id, operation, response) -> Any:
        """Checks the response against the operation's documented responses, and gives the parsed
        JSON body when there is one.
        """
        responses = operation.get("responses", {})
        status = response.status_code
        documented = next(
            (responses[key] for key in (str(status), f"{status // 100}XX", "default") if key in responses),
            None,
        )
        if documented is None:
            raise ExpectationFailure(
                f"Response status {status} is not documented for operation '{operation_id}'",
                f"One of the documented responses: {', '.join(responses) or 'none'}",
                status,
            )
        for name, header in documented.get("headers", {}).items():
            if header.get("required", False) and name.lower() != "content-type" and name not in response.headers:
                raise ExpectationFailure(
                    f"Response is missing the header '{name}', which the OpenAPI document requires",
                    f"Header {name}",
                    ", ".join(response.headers.keys()) or "no headers",
                )
        actual_type = response.headers.get("content-type", "").split(";")[0].strip()
        json_body = _NOT_JSON
        if actual_type and is_json_media_type(actual_type):
            # The document's schemas check JSON, so the body is converted from JSON whatever its type
            try:
                json_body = self._runner.get_converter(None).deserialize(response.content)
            except ExpectationFailure as e:
                raise ExpectationFailure(
                    f"Response body is not valid JSON although its Content-Type is {actual_type}",
                    "A JSON body",
                    response.text,
                ) from e
        if content := documented.get("content"):
            matched = next((m for m in content if actual_type and media_type_matches(m, actual_type)), None)
            if matched is None:
                raise ExpectationFailure(
                    f"Response content type '{actual_type or '(none)'}' is not documented for the "
                    f"{status} response of operation '{operation_id}'",
                    f"One of: {', '.join(content)}",
                    actual_type or "(none)",
                )
            schema = content[matched].get("schema")
            if schema is not None and json_body is not _NOT_JSON:
                try:
                    schema_validator(service.openapi_version, schema).validate(json_body)
                except JsonSchemaValidationError as e:
                    raise ExpectationFailure(
                        f"Response body does not match the OpenAPI schema for the {status} response "
                        f"of operation '{operation_id}': {e}",
                        f"A body matching the schema for {matched}",
                        response.text,
                    ) from e
        return json_body

    def _check_expectations(self, response, json_body):
        expected_status = self._expectations.get("status_code")
        if expected_status is not None and response.status_code != expected_status:
            raise ExpectationFailure("Status code", expected_status, response.status_code)
        if self._js_expect.json_schema is not None:
            if json_body is _NOT_JSON:
                raise ExpectationFailure(
                    "Response body is not JSON, so it can't be checked against expect.json_schema",
                    "A JSON body",
                    response.text,
                )
            self._js_expect.validate(json_body)


# A response body that isn't JSON, distinct from a body that is JSON null
_NOT_JSON = object()
