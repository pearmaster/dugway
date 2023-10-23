from typing import Any
from meta import JsonSchemaDefinedClass, JsonSchemaType, JsonConfigType
import json

class JsonSchemaDefinedCapability(JsonSchemaDefinedClass):
    
    def __init__(self, name: str, runner, config: dict[str, Any]):
        super().__init__(config)
        self._name = name
        self._runner = runner

    @property
    def name(self):
        return self._name


class JsonResponseBodyCapability(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("JsonResponseBody", runner, config)
        self._response_body: dict[str, Any]|None = None

    @property
    def json_response_body(self) -> dict[str, Any]|None:
        return self._response_body
    
    @json_response_body.setter
    def json_response_body(self, json_resp_body: dict[str, Any]):
        self._response_body = json_resp_body

    def set_json_response_from_string(self, json_text: str):
        self._response_body = json.loads(json_text)

    def get_config_schema(self) -> JsonSchemaType:
        return True


class ServiceDependency(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("ServiceDependency", runner, config)

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "service":{
                    "type": "string"
                }
            },
            "required": ["service"]
        }

    def get_service(self):
        return self._runner.get_service(self._config.get('service'))