
from .meta import JsonSchemaDefinedClass, JsonConfigType, JsonSchemaType
from .capabilities import JsonSchemaDefinedCapability
from .expectations import InvalidTestConfig

class JsonSchemaDefinedObject(JsonSchemaDefinedClass):
    
    def __init__(self, config: JsonConfigType, capabilities=None):
        if capabilities is None:
            self._capabilities = dict()
        else:
            self._capabilities = {cap.name: cap for cap in capabilities}
        super().__init__(config)
    
    def add_capability(self, capability: JsonSchemaDefinedCapability):
        self._capabilities[capability.name] = capability

    def has_capability(self, capability_name) -> bool:
        return capability_name in self._capabilities

    def get_capability(self, capability_name: str) -> JsonSchemaDefinedCapability:
        try:
            cap = self._capabilities[capability_name]
        except KeyError:
            raise InvalidTestConfig("Capability not found")
        return cap

    def find_capability(self, capability_name: str) -> JsonSchemaDefinedCapability|None:
        try:
            cap = self._capabilities[capability_name]
        except KeyError:
            return None
        return cap

    @classmethod
    def get_generic_schema(cls) -> JsonSchemaType:
        return True

    def get_object_schema(self) -> JsonSchemaType:
        return True

    def get_config_schema(self) -> JsonSchemaType:
        """ Returns the complete schema for the JSON provided to the object.
        This includes the schemas provided by capabilities, and a generic schema
        that applies even when this base class is specialized.
        """
        allof_list = list()
        for cap in self._capabilities.values():
            cap_schema = cap.get_config_schema()
            if cap_schema is not True:
                allof_list.append(cap_schema)
        if self.get_object_schema() is not True:
            allof_list.append(self.get_object_schema())
        if self.get_generic_schema() is not True:
            allof_list.append(self.get_generic_schema())
        
        return {
            "allOf": allof_list
        }