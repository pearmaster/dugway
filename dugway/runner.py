
from typing import Any
from abc import abstractmethod
import os
import expectations

from jacobsjsondoc.document import create_document
from stevedore import driver
from jinja2 import Environment as Jinja2Environment

from meta import JsonSchemaDefinedClass, JsonSchemaType, JsonConfigType
from capabilities import JsonSchemaDefinedCapability

class JsonSchemaDefinedObject(JsonSchemaDefinedClass):

    def __init__(self, config: JsonConfigType, capabilities=None):
        if capabilities is None:
            self._capabilities = dict()
        else:
            print(f"{capabilities=}")
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

    def __init__(self, runner, config: JsonConfigType, capabilities=[]):
        super().__init__(config=config, capabilities=capabilities)
        self._runner = runner

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
        pass

    def reset(self):
        pass

    def teardown(self):
        pass


class TestStep(JsonSchemaDefinedObject):
    
    def __init__(self, runner: 'TestRunner', config: JsonConfigType, capabilities: list[JsonSchemaDefinedCapability]|None=None):
        super().__init__(config, capabilities)
        self._runner = runner
    
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

    def run(self):
        for service in self._services.values():
            service.setup()
        for case_name, test_case in self._cases.items():
            print(f"Running test case: {case_name}")
            self._current_case = test_case
            test_case.run()
            for service in self._services.values():
                service.reset()
        for service in self._services.values():
            service.teardown()

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


class TestRunner:

    def __init__(self, filename):
        self._config = create_document(uri=filename)
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
    test_yaml = "examples/http_request.yaml"
    tr = TestRunner(test_yaml)
    tr.run()