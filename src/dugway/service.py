

import logging

from .meta_class import JsonSchemaDefinedObject, JsonConfigType, JsonSchemaType

class Service(JsonSchemaDefinedObject):
    """ A service is a web server or MQTT broker or connection to something else.  
    It is static, meaning that it is available to all the tests and test steps without
    changing.

    A test may have multiple services of same or varying types.

    This is a base class.  More concrete classes, such as an MQTT connection service,
    should inherit from this base class.
    """

    def __init__(self, runner, config: JsonConfigType, capabilities=[]):
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
                },
            },
            "required": [
                "type",
            ],
        }

    def setup(self):
        """ This is called once at the beginning of testing.  For example, to make a persistent connection
        to a broker.
        """
        pass

    def reset(self):
        """ This is called between tests to reset any data.  For example, to clear cookies or subscriptions.
        """
        pass

    def teardown(self):
        """ This is called at the end of testing.  For example, to disconnect a persistent connection.
        """
        pass
