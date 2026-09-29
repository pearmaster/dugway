import logging
from abc import abstractmethod
from fnmatch import fnmatchcase
from typing import Any

from jacobsjsonschema.draft7 import JsonSchemaValidationError
from jacobsjsonschema.draft7 import Validator as JsonSchemaValidator

from .expectations import InvalidTestConfig
from .meta import JsonConfigType, JsonSchemaType
from .meta_class import JsonSchemaDefinedObject


class Converter(JsonSchemaDefinedObject):
    """A converter turns values, like the JSON values written in a suite file, into the bytes that are
    sent, and turns bytes that are received back into values.

    A suite names the converters it uses under 'converters', each with its type and config, and steps
    that send or receive content give one's name as 'converter'. A step that isn't given a converter
    sends JSON, and converts content it receives with the converter that consumes the content's type,
    such as an HTTP response's Content-Type. Content without a type is converted as JSON. A step can
    also give options for the conversion as 'converterConfig', which the converter's config_schema
    defines.

    This is a base class. Converter types, such as protobuf, inherit from it and are installed with
    the 'dugwayconverter' entry point.
    """

    # The media type of the serialized bytes, such as application/json, when there is one. It is sent
    # as an HTTP request's Content-Type.
    content_type: str | None = None

    # The media types of content this converter deserializes, such as application/json. They may use
    # '*' as a wildcard, such as application/*+json.
    consumes: tuple[str, ...] = ()

    def __init__(self, runner, config: JsonConfigType, capabilities=None):
        super().__init__(config=config, capabilities=capabilities)
        self._runner = runner
        self._logger = logging.getLogger(__class__.__name__)

    @classmethod
    def get_generic_schema(cls) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "The converter type, such as json.",
                },
            },
            "required": [
                "type",
            ],
        }

    def consumes_type(self, content_type: str) -> bool:
        """Whether this converter deserializes content of the media type, such as a Content-Type header's
        value. Parameters, such as '; charset=utf-8', and case are ignored.
        """
        media_type = content_type.split(";", 1)[0].strip().lower()
        return any(fnmatchcase(media_type, pattern.lower()) for pattern in self.consumes)

    @classmethod
    def config_schema(cls) -> JsonSchemaType:
        """The JSON Schema for the config that serialize and deserialize take, which a step gives as
        'converterConfig'. Converters that take options override this, and by default there are none.
        """
        return {"type": "object", "additionalProperties": False}

    @classmethod
    def check_config(cls, config: JsonConfigType | None) -> None:
        """Raises InvalidTestConfig unless the config complies with config_schema. No config is checked as
        an empty one, so a schema's required options must be given.
        """
        try:
            JsonSchemaValidator(cls.config_schema()).validate({} if config is None else config)
        except JsonSchemaValidationError as e:
            raise InvalidTestConfig(f"Invalid converterConfig: {e}") from e

    @abstractmethod
    def serialize(self, value: Any, config: JsonConfigType | None = None) -> bytes:
        """Converts a value into the bytes to send, with the options in the config, which complies with
        config_schema. Raises FailedTestStep when the value can't be converted.
        """
        ...

    @abstractmethod
    def deserialize(self, data: bytes, config: JsonConfigType | None = None) -> Any:
        """Converts bytes that were received into a value, with the options in the config, which complies
        with config_schema. Raises ExpectationFailure when the bytes aren't in the converter's format.
        """
        ...
