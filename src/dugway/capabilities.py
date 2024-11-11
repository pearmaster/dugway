from typing import Any
import json
from queue import Queue, Empty as QueueEmpty

from jacobsjsonschema.draft7 import Validator as JsonSchemaValidator

from .meta import JsonSchemaDefinedClass, JsonSchemaType, JsonConfigType


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
        self._messages = Queue()

    @property
    def count(self):
        return self._messages.qsize()

    def get(self) -> dict[str, Any]:
        return self._messages.get()

    def get_or_none(self) -> dict[str, Any]|None:
        try:
            self._messages.get_nowait()
        except QueueEmpty:
            return None

    def add_message(self, json_resp: dict[str, Any]):
        self._messages.put(json_resp)

    def get_config_schema(self) -> JsonSchemaType:
        return True
    
    def __repr__(self) -> str:
        return f"<Capability {self._name} {self._messages.qsize()} message count>"

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
    

class JsonSchemaExpectation(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("JsonSchemaExpect", runner, config)
        self.json_schema = self._config["expect"]["json_schema"]
    
    def get_config_schema(self) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "expect":{
                    "type": "object",
                    "properties": {
                        "json_schema": {"type":"object"},
                    },
                }
            },
        }
    
    def check_against_json_schema(self, data: dict[str, Any]):
        if "expect" not in self._config and "json_schema" not in self._config["expect"]:
            return True
        validator = JsonSchemaValidator(self.json_schema)
        try:
            validator.validate(data)
        except Exception as e:
            raise e
            return False
        return True
    
class JsonSchemaFilter(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("JsonSchemaFilter", runner, config)
    
    def get_config_schema(self) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "filter":{
                    "type": "object",
                    "properties": {
                        "json_schema": {"type":"object"},
                    },
                }
            },
        }
    
    def check_against_json_schema(self, json_text: str):
        if "filter" not in self._config or "json_schema" not in self._config["filter"]:
            return True
        try:
            json_value = json.loads(json_text)
        except:
            return False
        validator = JsonSchemaValidator(self._config["filter"]["json_schema"])
        try:
            validator.validate(json_value)
        except Exception as e:
            return False
        return True