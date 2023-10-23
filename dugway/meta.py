
from typing import Any
from abc import ABC, abstractmethod
from jacobsjsonschema.draft7 import Validator as JsonSchemaValidator

JsonConfigType = dict[str,Any]
JsonSchemaType = bool|dict[str,Any]

class JsonSchemaDefinedClass(ABC):

    def __init__(self, config: JsonConfigType):
        self._config = config
        self.config_complies_with_schema(self._config)

    @abstractmethod
    def get_config_schema(self) -> JsonSchemaType:
        ...

    def config_complies_with_schema(self, config: JsonConfigType) -> bool:
        validator = JsonSchemaValidator(self.get_config_schema())
        validator.validate(config) # Throws exceptions if invalid
        return True
