from typing import Any
import json
from queue import Queue, Empty as QueueEmpty

from jacobsjsonschema.draft7 import Validator as JsonSchemaValidator

from .meta import JsonSchemaDefinedClass, JsonSchemaType, JsonConfigType, JsonContentType

class ContentWithProperties:

    def __init__(self, content, properties: dict[str, Any]|None=None):
        self.content = content
        self.properties = properties or dict()

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


class JsonContentCapability(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("JsonContent", runner, config)
        self._content = ContentWithProperties(None)

    @property
    def json_content(self) -> JsonContentType|None:
        return self._content.content
    
    @json_content.setter
    def json_content(self, json_resp_body: JsonContentType):
        self._content.content = json_resp_body

    def set_json_response_from_string(self, json_text: str):
        self.json_content = json.loads(json_text)

    def get_config_schema(self) -> JsonSchemaType:
        return True

class TextContentCapability(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("TextContent", runner, config)
        self._content = ContentWithProperties(None)

    @property
    def response_body(self) -> str|None:
        return self._content.content
    
    @response_body.setter
    def response_body(self, resp_body: str, properties: dict[str, Any]|None=None):
        self._content.content = resp_body
        if properties:
            self._content.properties = properties

    @property
    def response_content(self) -> ContentWithProperties|None:
        if self._content.content is None:
            return None
        return self._content

    def get_config_schema(self) -> JsonSchemaType:
        return True


class TextMultiContentCapability(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("TextMultiContent", runner, config)
        self._messages = Queue()

    @property
    def count(self):
        return self._messages.qsize()

    def get(self) -> str:
        return self.get_content().content

    def get_content(self) -> ContentWithProperties:
        return self._messages.get()

    def get_or_none(self) -> str|None:
        content = self.get_content_or_none()
        if content:
            content = content.content
        return content

    def get_content_or_none(self) -> ContentWithProperties|None:
        try:
            self._messages.get_nowait()
        except QueueEmpty:
            return None

    def add_content(self, content: str, properties: dict[str, Any]|None=None):
        content_with_props = ContentWithProperties(content, properties)
        self._messages.put(content_with_props)

    def get_config_schema(self) -> JsonSchemaType:
        return True
    
    def __repr__(self) -> str:
        return f"<TextMultiContent {self._name} {self._messages.qsize()} message count>"

class JsonMultiContentCapability(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("JsonMultiContentCapability", runner, config)
        self._messages = Queue()

    @property
    def count(self):
        return self._messages.qsize()

    def get(self) -> JsonContentType:
        return self.get_content().content

    def get_content(self) -> ContentWithProperties:
        return self._messages.get()

    def get_or_none(self) -> JsonContentType|None:
        content = self.get_content_or_none()
        if content:
            content = content.content
        return content

    def get_content_or_none(self) -> ContentWithProperties|None:
        try:
            self._messages.get_nowait()
        except QueueEmpty:
            return None

    def add_content(self, json_resp: JsonContentType, properties: dict[str, Any]|None=None):
        content_with_props = ContentWithProperties(json_resp, properties)
        self._messages.put(content_with_props)

    def get_config_schema(self) -> JsonSchemaType:
        return True
    
    def __repr__(self) -> str:
        return f"<JsonMultiContentCapability {self._name} {self._messages.qsize()} message count>"

class ValueCapability(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("Value", runner, config)
        self._value = None
        self._is_set = False

    def get(self) -> Any|None:
        return self._value
    
    def set(self, value: Any):
        self._value = value
        self._is_set = True
    
    @property
    def is_set(self) -> bool:
        return self._is_set

    def get_config_schema(self) -> JsonSchemaType:
        return True
    
    def __repr__(self) -> str:
        return f"<Value {self._value}>"

class MultiValueCapability(JsonSchemaDefinedCapability):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__("MultiValue", runner, config)
        self._value = Queue()

    @property
    def count(self):
        return self._value.qsize()

    def get(self) -> Any:
        return self._value.get()

    def get_or_none(self) -> Any|None:
        try:
            self._value.get_nowait()
        except QueueEmpty:
            return None

    def add_content(self, value: Any):
        self._value.put(value)

    def get_config_schema(self) -> JsonSchemaType:
        return True
    
    def __repr__(self) -> str:
        return f"<MultiValue {self._name} {self._messages.qsize()} message count>"

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