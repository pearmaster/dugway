
from abc import abstractmethod
import logging

from .meta import JsonConfigType, JsonSchemaType
from .meta_class import JsonSchemaDefinedObject
from .capabilities import JsonSchemaDefinedCapability

class TestStep(JsonSchemaDefinedObject):
    """ A test case is made up of 1+ test steps.  A test step is an individual bit of instruction to do something.
    Test steps are performed sequentially.  
    
    Some test steps maintain a dynamic state after the step is performed.
    For example, and MQTT subscriptiopn test step may continue to receive messages even though subsequent steps
    are being performed.  This is what differentiates a Dugway test from other test frameworks.

    Subsequent test steps can reference previous ones.  For example a "message received" test step may
    find received messages from a previous "subscribe to messages" test step.
    """
    
    def __init__(self, runner, config: JsonConfigType, capabilities: list[JsonSchemaDefinedCapability]|None=None):
        super().__init__(config, capabilities)
        self._runner = runner
        self._logger = logging.getLogger(__class__.__name__)
    
    def get_name(self, dfault:str=''):
        return self._config.get('id', dfault)

    @classmethod
    def get_generic_schema(cls) -> JsonSchemaType:
        return {
            "properties": {
                "type": {
                    "type": "string",
                },
                "id": {
                    "type": "string",
                }
            },
            "required": [
                "type",
            ],
        }
    
    @abstractmethod
    def run(self):
        ...
