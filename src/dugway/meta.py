
from typing import Any
from abc import ABC, abstractmethod
from jacobsjsonschema.draft7 import (
    Validator as JsonSchemaValidator,
    JsonSchemaValidationError
)
from .expectations import InvalidTestConfig

JsonConfigType = dict[str,Any]
JsonSchemaType = bool|dict[str,Any]

class JsonSchemaDefinedClass(ABC):
    """ This is an abstract base class for an object which is defined by a config dictionary,
    and the contents of that dictionary are defined by a JSON Schema.
    """

    def __init__(self, config: JsonConfigType):
        self._config = config
        # This will throw if the config does not conform to the schema.
        self.config_complies_with_schema(self._config)

    @abstractmethod
    def get_config_schema(self) -> JsonSchemaType:
        """ Inheriting classes must implement this method which returns a Python dictionary
        representation of the JSON Schema.
        """
        ...

    def config_complies_with_schema(self, config: JsonConfigType) -> bool:
        """ Checks that the config confirms to the schema.
        """
        validator = JsonSchemaValidator(self.get_config_schema())
        try:
            validator.validate(config) # Throws exceptions if invalid
        except JsonSchemaValidationError as e:
            raise InvalidTestConfig(f"Invalid test config: {e}")
        return True
