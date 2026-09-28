from copy import copy

import httpx

from .capabilities import ServiceDependency, TextContentCapability
from .expectations import ExpectationFailure
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

    The response body is available to later steps, such as a json step that gives this step's id
    as 'from'.
    """

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        self.serv_dep = ServiceDependency(runner, config)
        resp_cap = TextContentCapability(runner, config)
        super().__init__(runner, config, [self.serv_dep, resp_cap])
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
                "json": {
                    # Allow any json
                    "description": "Request body, sent as JSON. Sets the Content-Type header to "
                    "application/json unless a header already sets it.",
                },
                "content": {
                    "type": "string",  # or allow a string
                    "description": "Request body, sent as text. Ignored when json is given.",
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
        }

    def run(self):
        http_service = self.serv_dep.get_service()
        method = self._config.get("method", "GET")
        self._runner._reporter.step_info(f"{method} Request", http_service.get_url(self._path))
        # Copied so that adding a Content-Type doesn't modify the step's config
        headers = dict(self._config.get("headers", {}))
        httpx_kwargs = {}
        # Checked by key because falsy bodies like {}, [], 0 and false are still bodies
        if "json" in self._config:
            if "content-type" not in [h.lower() for h in headers]:
                headers["Content-Type"] = "application/json"
            httpx_kwargs["json"] = self._config["json"]
        elif "content" in self._config:
            httpx_kwargs["content"] = self._config["content"]
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
        self.get_capability(TextContentCapability.NAME).response_body = resp.text
