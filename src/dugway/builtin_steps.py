
from .step import TestStep
from .meta import JsonConfigType, JsonSchemaType
from time import sleep

class Sleep(TestStep):
    
    def __init__(self, runner, config: JsonConfigType):
        super().__init__(runner, config)
        self._time_to_sleep = int(config.get('time', 1))

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "time": {
                    "type": "integer",
                },
            },
        }

    def run(self):
        sleep(self._time_to_sleep)

BUILTIN_STEPS = {
    'sleep': Sleep,
}