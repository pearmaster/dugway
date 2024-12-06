
from typing import Any, Tuple, Iterator
from abc import abstractmethod
import os
import logging

from jacobsjsondoc.document import create_document
from jacobsjsondoc.options import ParseOptions, RefResolutionMode
from stevedore import driver
from jinja2 import Environment as Jinja2Environment

from .meta import JsonSchemaType, JsonConfigType
from .meta_class import JsonSchemaDefinedObject
from .reporter import AbstractReporter
from .case import TestCase

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

class TestSuite(JsonSchemaDefinedObject):
    """ In Dugway, a TestSuite is the largest component, being defined by a JSON/YAML file.
    A TestSuite contains 1+ services and 1+ test cases.
    """

    def __init__(self, name: str, runner, config: dict[str, Any], reporter: AbstractReporter):
        super().__init__(config)
        self._name = os.path.basename(name)
        self._runner = runner
        self._services: dict[str, Service] = dict()
        self._cases: dict[str, TestCase] = dict()
        self._reporter = reporter
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
            the_case = TestCase(case_name, self._runner, case_config, self._reporter)
            self._cases[case_name] = the_case
        self._current_case = None

    @property
    def name(self):
        return self._name

    def get_service(self, service_name: str) -> Service:
        return self._services[service_name]

    def do_setup(self):
        for service_name, service in self._services.items():
            self._reporter.add_service
            self._reporter.add_service(service_name)
            service.setup()

    def do_teardown(self):
        for service in self._services.values():
            service.teardown()

    def iterate_test_cases(self) -> Iterator[Tuple[str, TestCase]]:
        for case_name, test_case in self._cases.items():
            yield (case_name, test_case)

    def do_test_case_execution(self, case_name: str, test_case: TestCase):
        self._reporter.start_case(case_name)
        self._current_case = test_case
        test_case.run()
        for service in self._services.values():
            service.reset()
        self._reporter.end_case(True)

    def run(self):
        self._reporter.start_suite(self._name)
        print(f"starting suite with {len(self._services)} services")
        self.do_setup()
        for case_name, test_case in self.iterate_test_cases():
            self.do_test_case_execution(case_name, test_case)
        self.do_teardown()
        self._reporter.end_suite(True)

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

    def __init__(self, filename, reporter: AbstractReporter):
        self.logger = logging.getLogger("DugwayRunner")
        self.logger.info("Loading test suite from %s", filename)
        opts = ParseOptions()
        opts.ref_resolution_mode = RefResolutionMode.RESOLVE_REFERENCES
        self._config = create_document(uri=filename, options=opts)
        self.globals = {
            'env': os.environ,
        }
        self.jinja2_env = Jinja2Environment()
        self.jinja2_env.globals.update(self.globals)
        self._reporter = reporter
        self._suite = TestSuite(filename, self, self._config, self._reporter)


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
        self._suite.run()
        

if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    test_yaml = "examples/http_request.dugway.yaml"
    tr = DugwayRunner(test_yaml)
    tr.run()
