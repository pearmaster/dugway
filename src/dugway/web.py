from typing import Any
from copy import copy

import httpx
from jacobsjsonschema.draft7 import Validator as JsonSchemaValidator

from .step import TestStep
from .runner import DugwayRunner
from .service import Service
from .meta import JsonSchemaType, JsonConfigType
from .capabilities import ServiceDependency, TextContentCapability
from .expectations import ExpectationFailure

class HttpService(Service):

    def __init__(self, runner, config):
        super().__init__(runner, config)
        self._headers = { k:self._runner.template_eval(v) for (k,v) in config.get('headers', dict()).items() }
        self._hostname = self._runner.template_eval(config.get('hostname'))
        self._tls = config.get('tls', False)
        self._port = config.get('port', 443 if self._tls else 80)

    @classmethod
    def get_headers_schema(cls):
        return {
            "type": "object",
            "additionalProperties": {
                "type": "string",
            },
        }

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "hostname": {
                    "type": "string",
                },
                "port": {
                    "type": "integer"
                },
                "tls": {
                    "type": "boolean",
                    "default": False
                },
                "headers":HttpService.get_headers_schema(),
            },
            "required": [
                "hostname",
            ],
        }
    
    def get_url(self, path: str) -> str:
        evaluated_path = self._runner.template_eval(path)
        url = f"http{self._tls and 's' or ''}://{self._hostname}:{self._port}{evaluated_path}"
        return url

    def make_request(self, method: str, path: str, **httpx_kwargs):
        all_headers = copy(self._headers)
        all_headers.update({ k:self._runner.template_eval(v) for (k,v) in httpx_kwargs.get('headers', dict())})
        url = self.get_url(path)
        httpx_kwargs['headers'] = all_headers
        resp = httpx.request(method, url, **httpx_kwargs)
        return resp


class HttpRequest(TestStep):

    def __init__(self, runner: DugwayRunner, config: JsonConfigType):
        self.serv_dep = ServiceDependency(runner, config)
        resp_cap = TextContentCapability(runner, config)
        super().__init__(runner, config, [self.serv_dep, resp_cap])
        self._path = config.get('path')
        self._method = config.get('method', 'GET')
        self._expectations = config.get('expect', dict())
    
    def get_config_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "headers": HttpService.get_headers_schema(),
                "method": {
                    "type": "string",
                    "enum": [
                        "GET", "POST", "PUT", "HEAD", "DELETE", "PATCH", "OPTIONS", 
                    ],
                    "default": "GET",
                },
                "path": {
                    "type": "string",
                },
                "follow_redirects": {
                    "type": "boolean",
                    "default": True
                },
                "json": True, # Allow any json
                "content": {
                    "type": "string", # or allow a string
                },
                "expect": {
                    "type": "object",
                    "properties": {
                        "status_code": {
                            "type": "integer",
                            "minimum": 200,
                            "maximum": 599,
                        }
                    },
                }
            },
            "required": [
                "path",
            ],
        }
    
    def run(self):
        http_service = self.serv_dep.get_service()
        method = self._config.get('method', 'GET')
        self._runner._reporter.step_info(f"{method} Request", http_service.get_url(self._path))
        headers = self._config.get('headers', dict())
        httpx_kwargs = dict()
        if json_body := self._config.get('json'):
            if 'content-type' not in [h.lower() for h in headers.keys()]:
                headers['Content-Type'] = 'application/json'
            httpx_kwargs['json'] = json_body
        elif raw_body := self._config.get('content'):
            httpx_kwargs['content'] = raw_body
        resp = http_service.make_request(
            method,
            self._path,
            headers=headers,
            follow_redirects=self._config.get('follow_redirects', True),
            **httpx_kwargs
        )
        self._runner._reporter.step_info(f"{resp.status_code} Response", resp.text)
        if expected_status_code := self._expectations.get('status_code'):
            if resp.status_code != expected_status_code:
                raise ExpectationFailure("Status code", expected_status_code, resp.status_code)
        self.get_capability("TextContent").response_body = resp.text
