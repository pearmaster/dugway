
from .step import TestStep
from .meta import JsonConfigType, JsonSchemaType
from time import sleep
from typing import Any
import json
import jsonpath
from .capabilities import JsonContentCapability, JsonMultiContentCapability, FromStep, JsonSchemaExpectation, ValueCapability, MultiValueCapability
from . import expectations
from .service import Service
class Sleep(TestStep):
    
    def __init__(self, runner, config: JsonConfigType):
        super().__init__(runner, config)
        self._time_to_sleep = int(runner.template_eval(config.get('time', 1)))

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
        self.json_content_cap = JsonContentCapability(runner, config)
        self.json_multi_cap = JsonMultiContentCapability(runner, config)
        from_step = FromStep(runner, config)
        self._js_expect = JsonSchemaExpectation(runner, config)
        super().__init__(runner, config, [from_step, self._js_expect, self.json_content_cap])

    def get_config_schema(self) -> JsonSchemaType:
        return dict()

    def check_json(self, json_data:dict[str,Any]):
        if not self._js_expect.check_against_json_schema(json_data):
            raise expectations.FailedTestStep("Message payload did not match json schema")
        
    def run(self):
        from_step = self.get_capability("FromStep").get_step()
        if textual := from_step.find_capability("TextContent"):
            resp_json = json.loads(textual.response_body)
            self.check_json(resp_json)
            self.json_content_cap.json_content = resp_json
        elif multi_textual := from_step.find_capability("TextMultiContent"):
            content = self.multi_textual.get_or_none()
            while content is not None:
                json_content = json.loads(content)
                self.check_json(json_content)
                self.json_multi_cap.add_content(json_content)
                content = self.multi_textual.get_or_none()
        else:
            raise expectations.FailedTestStep("The 'from' step did not provide a textual response body")

class JsonPath(TestStep):

    def __init__(self, runner, config: JsonConfigType):
        self.value_cap = ValueCapability(runner, config)
        self.multi_value_cap = MultiValueCapability(runner, config)
        self.from_step = FromStep(runner, config)
        super().__init__(runner, config, [self.from_step, self.value_cap, self.multi_value_cap])
        self._match_count = 0
        self._match_path = "Match"

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "oneOf": [
                {
                    "properties": {
                        "path": {
                            "type": "string",
                        }
                    },
                    "required": ["path"],
                },
                {
                    "properties": {
                        "pointer": {
                            "type": "string"
                        }
                    },
                    "required": ["pointer"],
                }
            ],
            "properties": {
                "minimum": {
                    "type": "integer",
                    "default": 0,
                },
                "maximum": {
                    "type": "integer",
                },
            }
        }
        
    def _search(self, data):
        if path := self._config.get("path"):
            self._match_path = path
            matches = jsonpath.finditer(path, data)
            for match in matches:
                if not self.value_cap.is_set:
                    self.value_cap.set(match.value)
                self.multi_value_cap.add_content(match.value)
                self._match_count += 1
        elif pointer_str := self._config.get("pointer"):
            self._match_path = pointer_str
            pointer = jsonpath.JSONPointer(pointer_str)
            value = pointer.resolve(data)
            if not self.value_cap.is_set:
                self.value_cap.set(value)
            self.multi_value_cap.add_content(value)
            self._match_count += 1

    def run(self):
        found_source = False
        if json_content_cap := self.from_step.get_step().find_capability("JsonContent"):
            found_source = True
            self._search(json_content_cap.json_content)
            self._runner._reporter.step_info(f"Match against '{self._match_path}'", str(self.value_cap.get()))
        if multi_json_content_cap := self.from_step.get_step().find_capability("JsonMultiContent"):
            found_source = True
            content = multi_json_content_cap.get_or_none()
            while content is not None:
                self._search(content)
                content = multi_json_content_cap.get_or_none()
        if not found_source:
            raise expectations.FailedTestStep(f"The 'from' step '{self.from_step.get_step().get_name()}' did not provide JSON content")
        min_matches = self._config.get("minimum", 0)
        if self._match_count < min_matches:
            raise expectations.FailedTestStep("Only found {self._match_count} matches but {min_matches} were required.")
        if max_matches := self._config.get("maximum", False) and self._match_count > max_matches:
            raise expectations.FailedTestStep("Found {self._match_count} matches but only {max_matches} are allowed.")

class ValueSave(TestStep):

    def __init__(self, runner, config: JsonConfigType):
        self.from_step = FromStep(runner, config)
        super().__init__(runner, config, [self.from_step])

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "suite": {
                    "type": "string",
                },
                "case": {
                    "type": "string",
                },
            }
        }
    
    def run(self):
        source = self.from_step.get_step().find_capability("Value")
        value = source.get()
        if var_name := self._config.get("suite", False):
            self._runner.add_variable(var_name, value)
        if var_name := self._config.get("case", False):
            self._runner.current_case.add_variable(var_name, value)

class AddService(TestStep):

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(runner, config, [self.from_step])

    def get_config_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "name": {
                    "type": "string"
                },
                "config": Service.get_generic_schema(),
            },
            "required": [
                "name",
                "config",
            ]
        }
    
    def run(self):
        service_name = self._config.get("name")
        service_config = self._config.get("config")
        self._runner.add_service(service_name, service_config)

BUILTIN_STEPS = {
    'sleep': Sleep,
    'json': ConvertToJson,
    'jsonpath': JsonPath,
    'save': ValueSave,
    "service": AddService,
}