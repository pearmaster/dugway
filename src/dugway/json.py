import json
from typing import Any

from .converter import Converter
from .expectations import ExpectationFailure
from .meta import JsonConfigType


class JsonConverter(Converter):
    """Converts values to and from JSON text, encoded as UTF-8.

    It consumes application/json, application/*+json and text/json. Steps that aren't given a
    converter use JSON for content without a type, so it only needs naming under 'converters' to be
    given explicitly, such as for JSON sent with another content type.
    """

    content_type = "application/json"
    consumes = (content_type, "application/*+json", "text/json")

    def serialize(self, value: Any, config: JsonConfigType | None = None) -> bytes:
        return json.dumps(value).encode()

    def deserialize(self, data: bytes, config: JsonConfigType | None = None) -> Any:
        try:
            return json.loads(data)
        except (json.decoder.JSONDecodeError, UnicodeDecodeError) as e:
            raise ExpectationFailure(f"Content was not JSON: {e}", "JSON", data) from None
