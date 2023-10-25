from typing import Any
from copy import copy

import httpx
from jacobsjsonschema.draft7 import Validator as JsonSchemaValidator

from runner import Service, TestStep, TestRunner
from meta import JsonSchemaType, JsonConfigType
from capabilities import ServiceDependency, JsonResponseBodyCapability
from expectations import ExpectationFailure

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
    
    def make_request(self, method: str, path: str, **httpx_kwargs):
        all_headers = copy(self._headers)
        all_headers.update({ k:self._runner.template_eval(v) for (k,v) in httpx_kwargs.get('headers', dict())})
        evaluated_path = self._runner.template_eval(path)
        url = f"http{self._tls and 's' or ''}://{self._hostname}:{self._port}{evaluated_path}"
        print(url)
        httpx_kwargs['headers'] = all_headers
        resp = httpx.request(method, url, **httpx_kwargs)
        return resp


class HttpRequest(TestStep):

    def __init__(self, runner: TestRunner, config: JsonConfigType):
        print("HttpRequest __init__")
        self.serv_dep = ServiceDependency(runner, config)
        json_resp_cap = JsonResponseBodyCapability(runner, config)
        super().__init__(runner, config, [self.serv_dep, json_resp_cap])
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
                "expect": {
                    "type": "object",
                    "properties": {
                        "status_code": {
                            "type": "integer",
                            "minimum": 200,
                            "maximum": 599,
                        },
                        "json_schema": {
                            "type": "object",
                        }
                    },
                    "additionalProperties": False,
                }
            },
            "required": [
                "path",
            ],
        }
    
    def run(self):
        http_service = self.serv_dep.get_service()
        resp = http_service.make_request(
            self._config.get('method', 'GET'),
            self._path,
            headers=self._config.get('headers', dict()),
        )
        if resp.status_code != self._expectations.get('status_code', resp.status_code):
            raise ExpectationFailure("Status code failure")
        if schema := self._expectations.get('json_schema', False):
            validator = JsonSchemaValidator(schema)
            validator.validate(resp.json()) # Throws exceptions if invalid
        self.get_capability("JsonResponseBody").json_response_body = resp.json()
