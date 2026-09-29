import json
from queue import Empty as QueueEmpty
from queue import Queue
from typing import Any

from jacobsjsonschema.draft7 import (
    JsonSchemaValidationError,
)
from jacobsjsonschema.draft7 import (
    Validator as JsonSchemaValidator,
)

from .expectations import ExpectationFailure
from .meta import (
    JsonConfigType,
    JsonContentType,
    JsonSchemaDefinedClass,
    JsonSchemaType,
)


class ContentWithProperties:

    def __init__(self, content, properties: dict[str, Any] | None = None):
        self.content = content
        self.properties = properties or {}


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


class RawContentCapability(JsonSchemaDefinedCapability):
    """Provides content as it was received, such as an HTTP response body, for later steps such as
    deserialize to convert.

    It may be text, such as JSON, or binary, such as protobuf, so steps that use it decode it themselves.
    """

    NAME = "RawContent"

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(self.NAME, runner, config)
        self._content = ContentWithProperties(None)

    @property
    def content(self) -> bytes | None:
        return self._content.content

    @content.setter
    def content(self, content: bytes):
        self._content.content = content

    def get_content(self) -> ContentWithProperties | None:
        """The content along with its properties, or None when there is no content yet."""
        if self._content.content is None:
            return None
        return self._content

    def set_content(self, content: bytes, properties: dict[str, Any] | None = None):
        self._content = ContentWithProperties(content, properties)

    def get_config_schema(self) -> JsonSchemaType:
        return True


class RawMultiContentCapability(JsonSchemaDefinedCapability):
    """Provides contents as they were received, such as MQTT messages, for later steps such as
    mqtt_message or deserialize to convert.

    They are kept in the order received and, like RawContent, may be text or binary.
    """

    NAME = "RawMultiContent"

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(self.NAME, runner, config)
        self._messages = Queue()
        self.errors: list[Exception] = []

    @property
    def count(self):
        return self._messages.qsize()

    def add_error(self, error: Exception):
        """Records a problem with a message that couldn't be added, so that the step
        consuming the messages can report it.
        """
        self.errors.append(error)

    def raise_first_error(self):
        if self.errors:
            raise self.errors[0]

    def get(self) -> bytes:
        return self.get_content().content

    def get_content(self) -> ContentWithProperties:
        return self._messages.get()

    def get_or_none(self) -> bytes | None:
        content = self.get_content_or_none()
        if content is not None:
            content = content.content
        return content

    def get_content_or_none(self) -> ContentWithProperties | None:
        try:
            return self._messages.get_nowait()
        except QueueEmpty:
            return None

    def add_content(self, content: bytes, properties: dict[str, Any] | None = None):
        content_with_props = ContentWithProperties(content, properties)
        self._messages.put(content_with_props)

    def get_config_schema(self) -> JsonSchemaType:
        return True

    def __repr__(self) -> str:
        return f"<RawMultiContent {self._name} {self._messages.qsize()} message count>"


class ValueCapability(JsonSchemaDefinedCapability):
    """Provides one value, such as a converted response body or the first match found, for later steps
    such as jsonpath or save.
    """

    NAME = "Value"

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(self.NAME, runner, config)
        self._value = None
        self._is_set = False

    def get(self) -> Any | None:
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
    """Provides values, such as converted messages or every match found, for later steps such as
    jsonpath, mqtt_message or serialize.
    """

    NAME = "MultiValue"

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(self.NAME, runner, config)
        self._values = Queue()
        self.errors: list[Exception] = []

    @property
    def count(self):
        return self._values.qsize()

    def add_error(self, error: Exception):
        """Records a problem with a value that couldn't be added, so that the step consuming the values
        can report it.
        """
        self.errors.append(error)

    def raise_first_error(self):
        if self.errors:
            raise self.errors[0]

    def get(self) -> Any:
        return self.get_content().content

    def get_content(self) -> ContentWithProperties:
        return self._values.get()

    def get_or_none(self) -> Any | None:
        content = self.get_content_or_none()
        return None if content is None else content.content

    def get_content_or_none(self) -> ContentWithProperties | None:
        try:
            return self._values.get_nowait()
        except QueueEmpty:
            return None

    def add_content(self, value: Any, properties: dict[str, Any] | None = None):
        """Adds a value, with properties such as the topic of the message it came from."""
        self._values.put(ContentWithProperties(value, properties))

    def get_config_schema(self) -> JsonSchemaType:
        return True

    def __repr__(self) -> str:
        return f"<MultiValue {self._name} {self._values.qsize()} value count>"


class ServiceDependency(JsonSchemaDefinedCapability):
    """Uses a service from the suite's services, given as 'service'."""

    NAME = "ServiceDependency"

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(self.NAME, runner, config)

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Name of the service to use, as given under the suite's services.",
                }
            },
            "required": ["service"],
        }

    def get_service(self):
        return self._runner.get_service(self._config.get("service"))


class ConversionCapability(JsonSchemaDefinedCapability):
    """Uses a converter from the suite's converters, given as 'converter', to send or receive content,
    with the options given as 'converterConfig'.
    """

    NAME = "Conversion"

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(self.NAME, runner, config)

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "converter": {
                    "type": "string",
                    "description": "Name of the converter to use, as given under the suite's converters. "
                    "Defaults to JSON for content that is sent, and to the converter that consumes the "
                    "content's type for content that is received.",
                },
                "converterConfig": {
                    "type": "object",
                    "description": "Options for the converter, as its type accepts. See the converter type's help "
                    "for them.",
                },
            },
        }

    @property
    def is_given(self) -> bool:
        """Whether the step names a converter, rather than choosing one by default."""
        return "converter" in self._config

    def get_converter(self):
        """The converter for content the step sends: the one it names, or else JSON."""
        return self._runner.get_converter(self._config.get("converter"))

    def converter_for(self, content: ContentWithProperties):
        """The converter for content the step received: the one it names, or else the one that consumes
        the content's type.
        """
        if self.is_given:
            return self.get_converter()
        return self._runner.converter_for(content.properties.get("contentType"))

    def config_for(self, converter) -> JsonConfigType | None:
        """The step's converterConfig, once checked against the converter's config schema."""
        config = self._config.get("converterConfig")
        converter.check_config(config)
        return config

    def serialize(self, value: Any) -> bytes:
        """Serializes a value the step sends, with its converter and converterConfig."""
        converter = self.get_converter()
        return converter.serialize(value, self.config_for(converter))

    def deserialize(self, content: ContentWithProperties) -> Any:
        """Deserializes content the step received, with its converter and converterConfig."""
        converter = self.converter_for(content)
        return converter.deserialize(content.content, self.config_for(converter))


class FromStep(JsonSchemaDefinedCapability):
    """Uses what an earlier step in the test case provides, given as 'from'."""

    NAME = "FromStep"

    def __init__(
        self,
        runner,
        config: JsonConfigType,
        required: bool = True,
        description: str = "The id of an earlier step in this test case, whose output this step uses.",
    ):
        self._required = required
        self._description = description
        super().__init__(self.NAME, runner, config)

    def get_config_schema(self) -> JsonSchemaType:
        schema = {
            "type": "object",
            "properties": {"from": {"type": "string", "description": self._description}},
        }
        if self._required:
            schema["required"] = ["from"]
        return schema

    @property
    def is_given(self) -> bool:
        return "from" in self._config

    def get_step(self):
        return self._runner.get_step(self._config.get("from"))


class JsonSchemaExpectation(JsonSchemaDefinedCapability):
    """Checks JSON values against the JSON Schema given as 'expect.json_schema'."""

    NAME = "JsonSchemaExpect"

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(self.NAME, runner, config)
        # 'expect' is shared with other capabilities, so it may be present without a schema
        self.json_schema = self._config.get("expect", {}).get("json_schema")

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "expect": {
                    "type": "object",
                    "description": "Checks made on the content.",
                    "properties": {
                        "json_schema": {
                            "type": "object",
                            "description": "Fail unless the JSON matches this JSON Schema.",
                        },
                    },
                }
            },
        }

    def validate(self, data: JsonContentType):
        """Raises ExpectationFailure if the data doesn't match the expected JSON Schema.
        Does nothing when no schema was given.
        """
        if self.json_schema is None:
            return
        validator = JsonSchemaValidator(self.json_schema)
        try:
            validator.validate(data)
        except JsonSchemaValidationError as e:
            raise ExpectationFailure(
                f"JSON did not match the JSON Schema: {e}",
                json.dumps(self.json_schema),
                json.dumps(data),
            ) from e


class JsonSchemaFilter(JsonSchemaDefinedCapability):
    """Ignores received messages that don't match the JSON Schema given as 'filter.json_schema'."""

    NAME = "JsonSchemaFilter"

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(self.NAME, runner, config)

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "description": "Ignore received messages that don't match.",
                    "properties": {
                        "json_schema": {
                            "type": "object",
                            "description": "Keep only messages whose JSON matches this JSON Schema.",
                        },
                    },
                }
            },
        }

    def check_against_json_schema(self, json_text: str | bytes):
        if "filter" not in self._config or "json_schema" not in self._config["filter"]:
            return True
        try:
            json_value = json.loads(json_text)
        except ValueError:
            return False
        validator = JsonSchemaValidator(self._config["filter"]["json_schema"])
        try:
            validator.validate(json_value)
        except JsonSchemaValidationError:
            return False
        return True


def single_value(step) -> tuple[bool, Any]:
    """Whether the step provides one value, such as a converted response body or the first match found,
    and the value.
    """
    value_cap = step.find_capability(ValueCapability.NAME)
    if value_cap is not None and value_cap.is_set:
        return True, value_cap.get()
    return False, None


def multi_values(step) -> "MultiValueCapability | None":
    """The values the step provides, such as converted messages, when it doesn't provide one value."""
    return step.find_capability(MultiValueCapability.NAME)
