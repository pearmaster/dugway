"""
Using example from https://docs.pytest.org/en/latest/example/nonpython.html#yaml-plugin
"""

import pytest
from dugway.runner import DugwayRunner
from dugway.reporter import NoOpReporter
from typing import Iterator


class DugwayCaseFailure(Exception):
    pass


class FailureCollectingReporter(NoOpReporter):
    """Remembers which steps failed in the current test case so that pytest can report them."""

    def __init__(self):
        self._current_step = None
        self.failures: list[str] = list()

    def start_case(self, case_name: str):
        self.failures = list()

    def start_step(self, step_name: str):
        self._current_step = step_name

    def step_failure(self, title, data=None):
        message = f"Step '{self._current_step}': {title}"
        if hasattr(data, "details"):
            message += f"\n{data.details()}"
        elif data is not None and str(data) != title:
            message += f"\n{data}"
        self.failures.append(message)


class DugwayTestItem(pytest.Item):

    def __init__(self, *, spec, **kwargs):
        super().__init__(**kwargs)
        self.spec = spec

    def runtest(self) -> None:
        self.parent.suite.do_setup()
        try:
            passed = self.parent.suite.do_test_case_execution(self.name, self.spec)
        finally:
            self.parent.suite.do_teardown()
        if not passed:
            raise DugwayCaseFailure(
                "\n".join(self.parent.reporter.failures) or "Test case failed"
            )

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, DugwayCaseFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"dugway case: {self.name}"


class DugwayFile(pytest.File):

    def collect(self) -> Iterator[DugwayTestItem]:
        self.reporter = FailureCollectingReporter()
        self.runner = DugwayRunner(str(self.path), self.reporter)
        self.suite = self.runner.get_suite()
        for case_name, test_case in self.suite.iterate_test_cases():
            yield DugwayTestItem.from_parent(self, name=case_name, spec=test_case)
