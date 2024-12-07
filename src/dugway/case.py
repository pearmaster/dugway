
from stevedore import driver

from .meta_class import JsonSchemaDefinedObject
from .step import TestStep
from .meta import JsonConfigType, JsonSchemaType
from .reporter import AbstractReporter
from .builtin_steps import BUILTIN_STEPS


class TestCase(JsonSchemaDefinedObject):
    """ A test suite is made up of 1+ test cases.  Each test case contains 1+ test steps.
    If all the test steps in a test case are successful, then the test case passes, otherwise
    it fails.
    """

    def __init__(self, name: str, runner, config: JsonConfigType, reporter: AbstractReporter):
        super().__init__(config)
        self._runner = runner
        self._steps_by_id = dict()
        self._steps: list[TestStep] = list()
        self._reporter = reporter
        for step_config in config.get('steps', []):
            if step_config['type'] in BUILTIN_STEPS:
                step = BUILTIN_STEPS[step_config['type']](runner, step_config)
                self._steps.append(step)
                if step_id := step_config.get('id'):
                    self._steps_by_id[step_id] = step
            else:
                step_mgr = driver.DriverManager(
                    namespace='dugwayteststep',
                    name=step_config['type'],
                    invoke_on_load=True,
                    invoke_kwds={
                        "runner": self._runner,
                        "config": step_config,
                    }
                )
                self._steps.append(step_mgr.driver)
                if step_id := step_config.get('id'):
                    self._steps_by_id[step_id] = step_mgr.driver
        self._current_step = None

    def get_step(self, step_id: str) -> TestStep:
        return self._steps_by_id[step_id]

    def run(self) -> bool:
        for i, test_step in enumerate(self._steps):
            self._current_step = test_step
            self._reporter.start_step(test_step.get_name(dfault=str(i)))
            try:
                test_step.run()
            except Exception as e:
                self._reporter.step_failure("Exception", e)
                return False
            else:
                self._reporter.end_step(True)
        return True

    @classmethod
    def get_generic_schema(cls) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": TestStep.get_generic_schema(),
                },
            },
            "required": [
                "steps",
            ],
        }
