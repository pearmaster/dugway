import json
from time import sleep
from typing import Any

import jsonpath

from . import expectations
from .capabilities import (
    FromStep,
    JsonContentCapability,
    JsonMultiContentCapability,
    JsonSchemaExpectation,
    MultiValueCapability,
    TextContentCapability,
    TextMultiContentCapability,
    ValueCapability,
)
from .meta import JsonConfigType, JsonSchemaType
from .service import Service
from .step import TestStep


class Sleep(TestStep):
    """Pauses the test case for a number of seconds."""

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(runner, config)

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "time": {
                    "type": ["integer", "string"],
                    "default": 1,
                    "description": "Seconds to pause. Templates are evaluated.",
                },
            },
        }

    def run(self):
        # Evaluated when run, so the time can come from a variable saved by an earlier step
        sleep(int(self._runner.template_eval(self._config.get("time", 1))))


class ConvertToJson(TestStep):
    """Parses text from an earlier step, such as an HTTP response body, as JSON.

    Later steps, such as jsonpath or mqtt_message, use the parsed JSON by giving this step's id as
    'from'.
    """

    def __init__(self, runner, config: JsonConfigType):
        self.json_content_cap = JsonContentCapability(runner, config)
        self.json_multi_cap = JsonMultiContentCapability(runner, config)
        from_step = FromStep(runner, config)
        self._js_expect = JsonSchemaExpectation(runner, config)
        super().__init__(
            runner,
            config,
            [from_step, self._js_expect, self.json_content_cap, self.json_multi_cap],
        )

    def get_object_schema(self) -> JsonSchemaType:
        return {}

    def check_json(self, json_data: dict[str, Any]):
        self._js_expect.validate(json_data)

    def run(self):
        from_step = self.get_capability(FromStep.NAME).get_step()
        if textual := from_step.find_capability(TextContentCapability.NAME):
            resp_json = json.loads(textual.response_body)
            self.check_json(resp_json)
            self.json_content_cap.json_content = resp_json
        elif multi_textual := from_step.find_capability(TextMultiContentCapability.NAME):
            content = multi_textual.get_or_none()
            while content is not None:
                json_content = json.loads(content)
                self.check_json(json_content)
                self.json_multi_cap.add_content(json_content)
                content = multi_textual.get_or_none()
        else:
            raise expectations.FailedTestStep("The 'from' step did not provide a textual response body")


class JsonPath(TestStep):
    """Finds values in the JSON from an earlier step, using a JSONPath or a JSON Pointer.

    The first value found can be saved with a save step. Fails when fewer than minimum or more
    than maximum values are found.
    """

    def __init__(self, runner, config: JsonConfigType):
        self.value_cap = ValueCapability(runner, config)
        self.multi_value_cap = MultiValueCapability(runner, config)
        self.from_step = FromStep(runner, config)
        super().__init__(runner, config, [self.from_step, self.value_cap, self.multi_value_cap])
        self._match_count = 0
        self._match_path = "Match"

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "oneOf": [
                {
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "JSONPath expression to search with, such as $.items[*].id",
                        }
                    },
                    "required": ["path"],
                },
                {
                    "properties": {
                        "pointer": {
                            "type": "string",
                            "description": "JSON Pointer to a single value, such as /items/0/id",
                        }
                    },
                    "required": ["pointer"],
                },
            ],
            "properties": {
                "minimum": {
                    "type": "integer",
                    "default": 0,
                    "description": "Fail when fewer values than this are found.",
                },
                "maximum": {
                    "type": "integer",
                    "description": "Fail when more values than this are found.",
                },
            },
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
        json_content_cap = self.from_step.get_step().find_capability(JsonContentCapability.NAME)
        if json_content_cap is not None and json_content_cap.json_content is not None:
            found_source = True
            self._search(json_content_cap.json_content)
            self._runner._reporter.step_info(f"Match against '{self._match_path}'", str(self.value_cap.get()))
        if multi_json_content_cap := self.from_step.get_step().find_capability(JsonMultiContentCapability.NAME):
            found_source = True
            multi_json_content_cap.raise_first_error()
            content = multi_json_content_cap.get_or_none()
            while content is not None:
                self._search(content)
                content = multi_json_content_cap.get_or_none()
        if not found_source:
            raise expectations.FailedTestStep(
                f"The 'from' step '{self.from_step.get_step().get_name()}' did not provide JSON content"
            )
        min_matches = self._config.get("minimum", 0)
        if self._match_count < min_matches:
            raise expectations.FailedTestStep(
                f"Only found {self._match_count} matches but {min_matches} were required."
            )
        if (max_matches := self._config.get("maximum")) is not None and self._match_count > max_matches:
            raise expectations.FailedTestStep(f"Found {self._match_count} matches but only {max_matches} are allowed.")


class ValueSave(TestStep):
    """Saves the value found by an earlier step, such as jsonpath, as a variable.

    Later templates use a suite variable as {{ suite.name }}, for the rest of the suite, and a case
    variable as {{ case.name }}, for the rest of the test case.
    """

    def __init__(self, runner, config: JsonConfigType):
        self.from_step = FromStep(runner, config)
        super().__init__(runner, config, [self.from_step])

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "suite": {
                    "type": "string",
                    "description": "Name of the suite variable to save the value as.",
                },
                "case": {
                    "type": "string",
                    "description": "Name of the test case variable to save the value as.",
                },
            }
        }

    def run(self):
        from_step = self.from_step.get_step()
        source = from_step.find_capability(ValueCapability.NAME)
        if source is None:
            raise expectations.FailedTestStep(f"The 'from' step '{from_step.get_name()}' does not provide a value")
        value = source.get()
        suite = self._runner.get_suite()
        if var_name := self._config.get("suite", False):
            suite.add_variable(var_name, value)
        if var_name := self._config.get("case", False):
            suite.current_case.add_variable(var_name, value)


class AddService(TestStep):
    """Adds a service partway through a test case, and sets it up."""

    def __init__(self, runner, config: JsonConfigType):
        super().__init__(runner, config)

    def get_object_schema(self) -> JsonSchemaType:
        return {
            "properties": {
                "name": {"type": "string", "description": "Name that later steps use for the service."},
                "config": {
                    **Service.get_generic_schema(),
                    "description": "The service's config, as it would be given under the suite's services.",
                },
            },
            "required": [
                "name",
                "config",
            ],
        }

    def run(self):
        service_name = self._config.get("name")
        service_config = self._config.get("config")
        suite = self._runner.get_suite()
        suite.add_service(service_name, service_config)
        self._runner._reporter.add_service(service_name)
        suite.get_service(service_name).setup()


BUILTIN_STEPS = {
    "sleep": Sleep,
    "json": ConvertToJson,
    "jsonpath": JsonPath,
    "save": ValueSave,
    "service": AddService,
}
