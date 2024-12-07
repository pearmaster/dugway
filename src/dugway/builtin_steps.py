
from .step import TestStep
from .meta import JsonConfigType, JsonSchemaType
from time import sleep
from typing import Any
import json
from .capabilities import JsonResponseBodyCapability, FromStep, JsonSchemaExpectation
from . import expectations
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

class ConvertToJson(TestStep):
    def __init__(self, runner, config: JsonConfigType):
        self.json_resp_cap = JsonResponseBodyCapability(runner, config)
        from_step = FromStep(runner, config)
        self._js_expect = JsonSchemaExpectation(runner, config)
        super().__init__(runner, config, [from_step, self._js_expect, self.json_resp_cap])

    def get_config_schema(self) -> JsonSchemaType:
        return dict()

    def check_json(self, json_data:dict[str,Any]):
        if not self._js_expect.check_against_json_schema(json_data):
            raise expectations.FailedTestStep("Message payload did not match json schema")
        
    def run(self):
        from_step = self.get_capability("FromStep").get_step()
        if textual := from_step.find_capability("TextualResponseBody"):
            resp_json = json.loads(textual.response_body)
        else:
            raise expectations.FailedTestStep("The 'from' step did not provide a textual response body")
        self.check_json(resp_json)
        self.json_resp_cap.json_response_body = resp_json


BUILTIN_STEPS = {
    'sleep': Sleep,
    'json': ConvertToJson
}