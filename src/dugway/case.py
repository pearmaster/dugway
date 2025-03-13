
from typing import Any, LiteralString

from stevedore import driver

from .meta_class import JsonSchemaDefinedObject
from .step import TestStep
from .meta import JsonConfigType, JsonSchemaType
from .reporter import AbstractReporter
from .builtin_steps import BUILTIN_STEPS
from .expectations import FailedTestStep

class TestCase(JsonSchemaDefinedObject):
    """ A test suite is made up of 1+ test cases.  Each test case contains 1+ test steps.
    If all the test steps in a test case are successful, then the test case passes, otherwise
    it fails.
    """

    def __init__(self, name: str, runner, config: JsonConfigType, reporter: AbstractReporter):
        super().__init__(config)
        self._name = name
        self._runner = runner
        self._steps_by_id = dict()
        self._setup: list[TestStep] = list()
        self._teardown: list[TestStep] = list()
        self._steps: list[TestStep] = list()
        self._reporter = reporter
        self._variables: dict[str, str|int|float|bool] = dict()
        self._add_steps(config, self._steps)
        self._current_step = None

    def _add_steps(self, config, dest_list: list[TestStep]):
        for step_config in config.get('steps', []):
            if step_config['type'] in BUILTIN_STEPS:
                step = BUILTIN_STEPS[step_config['type']](self._runner, step_config)
                dest_list.append(step)
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
                dest_list.append(step_mgr.driver)
                if step_id := step_config.get('id'):
                    self._steps_by_id[step_id] = step_mgr.driver

    def add_setup(self, setup_config):
        self._add_steps(setup_config, self._setup)

    def add_teardown(self, teardown_config):
        self._add_steps(teardown_config, self._teardown)

    def add_variable(self, var_name: str, var_value: str|int|float|bool):
        self._variables[var_name] = var_value

    def get_step(self, step_id: str) -> TestStep:
        return self._steps_by_id[step_id]

    def _run_step(self, i: int, test_step: TestStep) -> bool:
        self._current_step = test_step
        self._reporter.start_step(test_step.get_name(dfault=str(i)))
        try:
            test_step.run()
        except FailedTestStep as e:
            self._reporter.step_failure("Exception", e)
            return False
        except Exception as e:
            self._reporter.step_failure("Exception", str(e))
            return False
        else:
            self._reporter.end_step(True)
        return True

    def run(self) -> bool:
        result = True
        for i, setup_step in enumerate(self._setup):
            if not self._run_step(i, setup_step):
                result = False
                break
        if result:
            for i, test_step in enumerate(self._steps):
                if not self._run_step(i, test_step):
                    result = False
                    print(f"running {test_step} result is {result}")
                    break
        for i, teardown_step in enumerate(self._teardown):
            if not self._run_step(i, teardown_step):
                result = False
                break
        if not result:
            self._reporter.end_case(False)
        return result

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
