from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from jacobsjsonschema.draft7 import (
    JsonSchemaValidationError,
)
from jacobsjsonschema.draft7 import (
    Validator as JsonSchemaValidator,
)

from .expectations import InvalidTestConfig

JsonConfigType = dict[str, Any]
JsonSchemaType = bool | dict[str, Any]
JsonContentType = dict[str, Any] | list[Any] | bool | int | float | str | None

_skip_config_validation: ContextVar[bool] = ContextVar("skip_config_validation", default=False)


@contextmanager
def without_config_validation() -> Iterator[None]:
    """Lets objects be built from placeholder config, so their schemas can be inspected."""
    token = _skip_config_validation.set(True)
    try:
        yield
    finally:
        _skip_config_validation.reset(token)


class JsonSchemaDefinedClass(ABC):
    """This is an abstract base class for an object which is defined by a config dictionary,
    and the contents of that dictionary are defined by a JSON Schema.
    """

    def __init__(self, config: JsonConfigType):
        self._config = config
        # This will throw if the config does not conform to the schema.
        if not _skip_config_validation.get():
            self.config_complies_with_schema(self._config)

    @abstractmethod
    def get_config_schema(self) -> JsonSchemaType:
        """Inheriting classes must implement this method which returns a Python dictionary
        representation of the JSON Schema.
        """
        ...

    def config_complies_with_schema(self, config: JsonConfigType) -> bool:
        """Checks that the config confirms to the schema."""
        validator = JsonSchemaValidator(self.get_config_schema())
        try:
            validator.validate(config)  # Throws exceptions if invalid
        except JsonSchemaValidationError as e:
            raise InvalidTestConfig(f"Invalid test config: {e}")
        return True
