from typing import Any
from meta import JsonSchemaDefinedClass, JsonSchemaType, JsonConfigType
import json
from collections import deque

class JsonSchemaDefinedCapability(JsonSchemaDefinedClass):
    
    def __init__(self, name: str, runner, config: dict[str, Any]):
        super().__init__(config)
        self._name = name
        self._runner = runner

    @property
    def name(self):
        return self._name
    
    def __repr__(self) -> str:
        return f"<Capability {self._name}>"


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


class JsonMultiResponseCapability(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("JsonMultiResponse", runner, config)
        self._responses: deque()

    def pop_oldest(self) -> dict[str, Any]:
        self._responses.popleft()    

    def pop_newest(self) -> dict[str, Any]:
        self._responses.pop()

    def add_response(self, json_resp: dict[str, Any]):
        self._responses.append(json_resp)

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
    

class FromStep(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("FromStep", runner, config)
    
    def get_config_schema(self) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "from":{
                    "type": "string"
                }
            },
            "required": ["from"]
        }
    
    def get_step(self):
        from_step_id = self._config.get('from')
        return self._runner.get_step(from_step_id)