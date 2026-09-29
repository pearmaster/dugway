from time import sleep
from typing import Any

import jsonpath

from . import expectations
from .capabilities import (
    ConversionCapability,
    FromStep,
    JsonSchemaExpectation,
    MultiValueCapability,
    RawContentCapability,
    RawMultiContentCapability,
    ValueCapability,
    multi_values,
    single_value,
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


class ConvertFrom(TestStep):
    """Converts content from an earlier step, such as an HTTP response body or the messages of an
    mqtt_subscribe step, into values, using a converter's deserializer.

    The content is converted with the given converter, such as a protobuf converter, or else with the
    one that consumes the content's type, or as JSON when it has no type. Later steps, such as
    jsonpath or mqtt_message, use the values by giving this step's id as 'from'.
    """

    def __init__(self, runner, config: JsonConfigType):
        self.value_cap = ValueCapability(runner, config)
        self.multi_value_cap = MultiValueCapability(runner, config)
        from_step = FromStep(runner, config)
        self._js_expect = JsonSchemaExpectation(runner, config)
        self._conversion_cap = ConversionCapability(runner, config)
        super().__init__(
            runner,
            config,
            [from_step, self._js_expect, self._conversion_cap, self.value_cap, self.multi_value_cap],
        )

    def get_object_schema(self) -> JsonSchemaType:
        return {}

    def check_json(self, json_data: dict[str, Any]):
        self._js_expect.validate(json_data)

    def run(self):
        from_step = self.get_capability(FromStep.NAME).get_step()
        if raw := from_step.find_capability(RawContentCapability.NAME):
            if (content := raw.get_content()) is None:
                raise expectations.FailedTestStep(f"The 'from' step '{from_step.get_name()}' has no content yet")
            value = self._conversion_cap.deserialize(content)
            self.check_json(value)
            self.value_cap.set(value)
        elif multi_raw := from_step.find_capability(RawMultiContentCapability.NAME):
            multi_raw.raise_first_error()
            content = multi_raw.get_content_or_none()
            while content is not None:
                value = self._conversion_cap.deserialize(content)
                self.check_json(value)
                # Kept with the value, so that mqtt_message can check the topic
                self.multi_value_cap.add_content(value, content.properties)
                content = multi_raw.get_content_or_none()
        else:
            raise expectations.FailedTestStep(
                f"The 'from' step '{from_step.get_name()}' did not provide content to convert"
            )


class ConvertTo(TestStep):
    """Converts values from an earlier step, such as a deserialize or jsonpath step, into content,
    using the given converter's serializer.

    Each value becomes content of the converter's type, keeping the properties it had, such as the
    topic it was received on. A later step, such as deserialize, uses the content by giving this
    step's id as 'from'.
    """

    def __init__(self, runner, config: JsonConfigType):
        self.raw_content_cap = RawContentCapability(runner, config)
        self.raw_multi_cap = RawMultiContentCapability(runner, config)
        from_step = FromStep(runner, config)
        self._conversion_cap = ConversionCapability(runner, config)
        super().__init__(
            runner,
            config,
            [from_step, self._conversion_cap, self.raw_content_cap, self.raw_multi_cap],
        )

    def get_object_schema(self) -> JsonSchemaType:
        return {"required": ["converter"]}

    def run(self):
        converter = self._conversion_cap.get_converter()

        def properties(given: dict[str, Any]) -> dict[str, Any]:
            return {**given, "contentType": converter.content_type}

        from_step = self.get_capability(FromStep.NAME).get_step()
        is_single, value = single_value(from_step)
        if is_single:
            self.raw_content_cap.set_content(self._conversion_cap.serialize(value), properties({}))
        elif multi := multi_values(from_step):
            multi.raise_first_error()
            content = multi.get_content_or_none()
            while content is not None:
                self.raw_multi_cap.add_content(
                    self._conversion_cap.serialize(content.content), properties(content.properties)
                )
                content = multi.get_content_or_none()
        else:
            raise expectations.FailedTestStep(
                f"The 'from' step '{from_step.get_name()}' did not provide values to convert"
            )


class JsonPath(TestStep):
    """Finds values in the JSON or value from an earlier step, using a JSONPath or a JSON Pointer.

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
        from_step = self.from_step.get_step()
        is_single, value = single_value(from_step)
        if is_single:
            self._search(value)
            self._runner._reporter.step_info(f"Match against '{self._match_path}'", str(self.value_cap.get()))
        elif multi := multi_values(from_step):
            multi.raise_first_error()
            content = multi.get_or_none()
            while content is not None:
                self._search(content)
                content = multi.get_or_none()
        else:
            raise expectations.FailedTestStep(f"The 'from' step '{from_step.get_name()}' did not provide values")
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
    "deserialize": ConvertFrom,
    "serialize": ConvertTo,
    "jsonpath": JsonPath,
    "save": ValueSave,
    "service": AddService,
}
