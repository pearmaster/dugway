from copy import copy

import httpx

from .capabilities import (
    ContentWithProperties,
    ConversionCapability,
    FromStep,
    RawContentCapability,
    ServiceDependency,
    ValueCapability,
)
from .expectations import ExpectationFailure, FailedTestStep, InvalidTestConfig
from .meta import JsonConfigType, JsonSchemaType
from .runner import DugwayRunner
from .service import Service
from .step import TestStep


class HttpService(Service):
    """An HTTP or HTTPS server, which http_request steps send requests to.

    Headers given here are sent with every request, along with each request's own headers.
    """

    def __init__(self, runner, config):
        super().__init__(runner, config)
        self._headers = {k: self._runner.template_eval(v) for (k, v) in config.get("headers", {}).items()}
        self._hostname = self._runner.template_eval(config.get("hostname", "")) or "localhost"
        self._tls = config.get("tls", False)
        self._port = config.get("port", 443 if self._tls else 80)

    @classmethod
    def get_headers_schema(cls):
        return {
            "type": "object",
            "additionalProperties": {
                "type": "string",
            },
        }

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "hostname": {
                    "type": "string",
                    "default": "localhost",
                    "description": "Server hostname or IP address. Templates are evaluated, "
                    "and an empty result means localhost.",
                },
                "port": {"type": "integer", "description": "Server port. Defaults to 443 with TLS, otherwise 80."},
                "tls": {"type": "boolean", "default": False, "description": "Use HTTPS instead of HTTP."},
                "headers": {
                    **HttpService.get_headers_schema(),
                    "description": "Headers sent with every request. Values are templates.",
                },
            },
        }

    def get_url(self, path: str) -> str:
        evaluated_path = self._runner.template_eval(path)
        url = f"http{self._tls and 's' or ''}://{self._hostname}:{self._port}{evaluated_path}"
        return url

    def make_request(self, method: str, path: str, **httpx_kwargs):
        all_headers = copy(self._headers)
        all_headers.update({k: self._runner.template_eval(v) for (k, v) in httpx_kwargs.get("headers", {}).items()})
        url = self.get_url(path)
        httpx_kwargs["headers"] = all_headers
        resp = httpx.request(method, url, **httpx_kwargs)
        return resp


class HttpRequest(TestStep):
    """Sends an HTTP request to an http service, and can check the response status code.

    Uses the provided converter to serialize the request and deserialize the response.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        self.serv_dep = ServiceDependency(runner, config)
        resp_cap = RawContentCapability(runner, config)
        self._conversion = ConversionCapability(runner, config)
        self._value_cap = ValueCapability(runner, config)
        self._from_step = FromStep(
            runner,
            config,
            required=False,
            description="The id of an earlier step which provides a value for the request payload",
        )
        super().__init__(runner, config, [self.serv_dep, resp_cap, self._conversion, self._value_cap, self._from_step])
        self._path = config.get("path")
        self._method = config.get("method", "GET")
        self._expectations = config.get("expect", {})

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "headers": {
                    **HttpService.get_headers_schema(),
                    "description": "Headers for this request, added to the service's headers. Values are templates.",
                },
                "method": {
                    "type": "string",
                    "enum": [
                        "GET",
                        "POST",
                        "PUT",
                        "HEAD",
                        "DELETE",
                        "PATCH",
                        "OPTIONS",
                    ],
                    "default": "GET",
                    "description": "HTTP method.",
                },
                "path": {
                    "type": "string",
                    "description": "Request path, added to the service's address. Templates are evaluated.",
                },
                "follow_redirects": {
                    "type": "boolean",
                    "default": True,
                    "description": "Follow redirect responses.",
                },
                "payload": {
                    # Any value, since the converter decides what it can serialize
                    "description": "Request body's value, serialized by the converter.",
                },
                "expect": {
                    "type": "object",
                    "description": "Checks made on the response.",
                    "properties": {
                        "status_code": {
                            "type": "integer",
                            "minimum": 200,
                            "maximum": 599,
                            "description": "Fail unless the response has this status code.",
                        }
                    },
                },
            },
            "required": [
                "path",
            ],
            "not": {"required": ["payload", "from"]},
        }

    @staticmethod
    def _set_content_type(headers: dict[str, str], content_type: str | None):
        if content_type and "content-type" not in [h.lower() for h in headers]:
            headers["Content-Type"] = content_type

    def _serialize(self, value, headers: dict[str, str]) -> bytes:
        self._set_content_type(headers, self._conversion.get_converter().content_type)
        return self._conversion.serialize(value)

    def _request_body(self, headers: dict[str, str]) -> bytes | None:
        """The request body, if there is one, setting its Content-Type in the headers unless they give one."""
        # Checked by key because falsy payloads like {}, [], 0 and false are still bodies
        if "payload" in self._config:
            return self._serialize(self._config["payload"], headers)
        if not self._from_step.is_given:
            return None
        source = self._from_step.get_step()
        raw = source.find_capability(RawContentCapability.NAME)
        if raw is not None and (content := raw.get_content()) is not None:
            # Already serialized, so it is sent as it is
            self._set_content_type(headers, content.properties.get("contentType"))
            return content.content
        value = source.find_capability(ValueCapability.NAME)
        if value is not None and value.is_set:
            return self._serialize(value.get(), headers)
        raise FailedTestStep(f"The 'from' step '{source.get_name()}' did not provide content or a value to send")

    def run(self):
        http_service = self.serv_dep.get_service()
        method = self._config.get("method", "GET")
        self._runner._reporter.step_info(f"{method} Request", http_service.get_url(self._path))
        # Copied so that adding a Content-Type doesn't modify the step's config
        headers = dict(self._config.get("headers", {}))
        httpx_kwargs = {}
        if (body := self._request_body(headers)) is not None:
            httpx_kwargs["content"] = body
        resp = http_service.make_request(
            method,
            self._path,
            headers=headers,
            follow_redirects=self._config.get("follow_redirects", True),
            **httpx_kwargs,
        )
        self._runner._reporter.step_info(f"{resp.status_code} Response", resp.text)
        expected_status_code = self._expectations.get("status_code")
        if expected_status_code and resp.status_code != expected_status_code:
            raise ExpectationFailure("Status code", expected_status_code, resp.status_code)
        resp_cap = self.get_capability(RawContentCapability.NAME)
        resp_cap.set_content(resp.content, {"contentType": resp.headers.get("content-type")})
        if resp.content:
            self._convert_response(resp_cap.get_content())

    def _convert_response(self, content: ContentWithProperties):
        if self._conversion.is_given:
            self._value_cap.set(self._conversion.deserialize(content))
            return
        # Without a converter the body may be anything, such as HTML, so it is only converted if it can be
        try:
            self._value_cap.set(self._conversion.deserialize(content))
        except (ExpectationFailure, InvalidTestConfig) as e:
            self._logger.debug("Response body was not converted: %s", e)
