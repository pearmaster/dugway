
from typing import Any, Tuple, Iterator
from abc import abstractmethod
import os
import logging

from jacobsjsondoc.document import create_document
from jacobsjsondoc.options import ParseOptions, RefResolutionMode
from stevedore import driver
from jinja2 import Environment as Jinja2Environment

from .meta import JsonSchemaDefinedClass, JsonSchemaType, JsonConfigType
from .capabilities import JsonSchemaDefinedCapability
from . import expectations

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
            raise expectations.InvalidTestConfig("Capability not found")
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


class TestStep(JsonSchemaDefinedObject):
    """ A test case is made up of 1+ test steps.  A test step is an individual bit of instruction to do something.
    Test steps are performed sequentially.  
    
    Some test steps maintain a dynamic state after the step is performed.
    For example, and MQTT subscriptiopn test step may continue to receive messages even though subsequent steps
    are being performed.  This is what differentiates a Dugway test from other test frameworks.

    Subsequent test steps can reference previous ones.  For example a "message received" test step may
    find received messages from a previous "subscribe to messages" test step.
    """
    
    def __init__(self, runner: 'DugwayRunner', config: JsonConfigType, capabilities: list[JsonSchemaDefinedCapability]|None=None):
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

class TestCase(JsonSchemaDefinedObject):
    """ A test suite is made up of 1+ test cases.  Each test case contains 1+ test steps.
    If all the test steps in a test case are successful, then the test case passes, otherwise
    it fails.
    """

    def __init__(self, name: str, runner, config: JsonConfigType):
        super().__init__(config)
        self._runner = runner
        self._steps_by_id = dict()
        self._steps: list[TestStep] = list()
        for step_config in config.get('steps', []):
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

    def run(self):
        for i, test_step in enumerate(self._steps):
            self._current_step = test_step
            print(f"Step: {test_step.get_name(str(i))}")
            test_step.run()

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

class TestSuite(JsonSchemaDefinedObject):
    """ In Dugway, a TestSuite is the largest component, being defined by a JSON/YAML file.
    A TestSuite contains 1+ services and 1+ test cases.
    """

    def __init__(self, name: str, runner, config: dict[str, Any]):
        super().__init__(config)
        self._name = os.path.basename(name)
        self._runner = runner
        self._services: dict[str, Service] = dict()
        self._cases: dict[str, TestCase] = dict()
        for service_name, service_config in config.get('services', dict()).items():
            service_type = service_config.get('type')
            service_mgr = driver.DriverManager(
                namespace='dugwayservice',
                name=service_type,
                invoke_on_load=True,
                invoke_kwds={
                    "runner": self._runner,
                    "config": service_config,
                }
            )
            self._services[service_name] = service_mgr.driver
        for case_key, case_config in config.get('testCases', dict()).items():
            case_name = case_config.get('name', case_key)
            the_case = TestCase(case_name, self._runner, case_config)
            self._cases[case_name] = the_case
        self._current_case = None

    @property
    def name(self):
        return self._name

    def get_service(self, service_name: str) -> Service:
        return self._services[service_name]

    def do_setup(self):
        for service in self._services.values():
            service.setup()

    def do_teardown(self):
        for service in self._services.values():
            service.teardown()

    def iterate_test_cases(self) -> Iterator[Tuple[str, TestCase]]:
        for case_name, test_case in self._cases.items():
            yield (case_name, test_case)

    def do_test_case_execution(self, case_name: str, test_case: TestCase):
        print(f"Running test case: {case_name}")
        self._current_case = test_case
        test_case.run()
        for service in self._services.values():
            service.reset()

    def run(self):
        self.do_setup()
        for case_name, test_case in self.iterate_test_cases():
            self.do_test_case_execution(case_name, test_case)
        self.do_teardown()

    @classmethod
    def get_generic_schema(cls) -> JsonSchemaType:
        return {
            "type": "object",
            "properties": {
                "testCases": {
                    "type": "object",
                    "additionalProperties": TestCase.get_generic_schema(),
                },
                "services": {
                    "type": "object",
                    "additionalProperties": Service.get_generic_schema(),
                }
            },
            "required": [
                "services",
                "testCases",
            ],
        }


class DugwayRunner:

    def __init__(self, filename):
        print(f"Loading test suite from: {filename}")
        opts = ParseOptions()
        opts.ref_resolution_mode = RefResolutionMode.RESOLVE_REFERENCES
        self._config = create_document(uri=filename, options=opts)
        self.globals = {
            'env': os.environ,
        }
        self.jinja2_env = Jinja2Environment()
        self.jinja2_env.globals.update(self.globals)
        self._suite = TestSuite(filename, self, self._config)

    def get_service(self, service_name: str):
        return self._suite.get_service(service_name)

    def get_step(self, step_id: str):
        return self._suite._current_case.get_step(step_id)
    
    def get_suite(self) -> TestSuite:
        return self._suite

    def template_eval(self, element: str|list[Any]|dict[str,Any], context:dict[str,Any]|None=None):
        if isinstance(element, str):
            template = self.jinja2_env.from_string(element)
            if context is None:
                return template.render()
            else:
                return template.render(context)
        elif isinstance(element, int):
            template = self.jinja2_env.from_string(str(element))
            if context is None:
                return int(template.render())
            else:
                return int(template.render(context))
        elif isinstance(element, bool):
            template = self.jinja2_env.from_string(str(element))
            if context is None:
                v = template.render()
            else:
                v = template.render(context)
            return v is True or v.lower == 'true' or v == 1

    def run(self):
        print(f"Running TestSuite: {self._suite.name}")
        self._suite.run()

if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    test_yaml = "examples/http_request.dugway.yaml"
    tr = DugwayRunner(test_yaml)
    tr.run()
