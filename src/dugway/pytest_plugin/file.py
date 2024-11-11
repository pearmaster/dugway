"""
Using example from https://docs.pytest.org/en/latest/example/nonpython.html#yaml-plugin
"""

import pytest
from dugway.runner import DugwayRunner
from typing import Iterator

class DugwayTestItem(pytest.Item):

    def __init__(self, *, spec, **kwargs):
        super().__init__(**kwargs)
        self.spec = spec

    def runtest(self) -> None:
        self.parent.suite.do_setup()
        self.parent.suite.do_test_case_execution(self.name, self.spec)
        self.parent.suite.do_teardown()


class DugwayFile(pytest.File):

    def collect(self) -> Iterator[DugwayTestItem]:
        self.runner = DugwayRunner(str(self.path))
        self.suite = self.runner.get_suite()
        for case_name, test_case in self.suite.iterate_test_cases():
            yield DugwayTestItem.from_parent(self, name=case_name, spec=test_case)
