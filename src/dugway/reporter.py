
from abc import ABC, abstractmethod
from functools import partial

import junit_xml
from rich.tree import Tree
from rich.live import Live
from rich.spinner import Spinner
from rich.console import Console
from rich.text import Text
from rich.emoji import Emoji
from rich.progress_bar import ProgressBar
from rich.columns import Columns
from rich import print

class AbstractReporter(ABC):

    @abstractmethod
    def start_suite(self, suite_name):
        ...

    def end_suite(self, result: bool):
        ...

    @abstractmethod
    def start_case(self, case_name: str):
        ...

    def end_case(self, result: bool):
        ...

    @abstractmethod
    def start_step(self, step_name: str):
        ...

    def end_step(self, result: bool):
        ...

    def add_service(self, service_name):
        ...


class MultiReporter:

    def __init__(self, reporters: list[AbstractReporter]):
        self.reporters = reporters

    def __getattr__(self, funcname):
        def method(*args, **kwargs):
            for reporter in self.reporters:
                func = getattr(reporter, funcname)
                func(*args, **kwargs)

        return method

    def start_suite(self, suite_name):
        for reporter in self.reporters:
            reporter.start_suite(suite_name)
    
    def start_case(self, case_name):
        for reporter in self.reporters:
            reporter.start_case(case_name)
    
    def start_step(self, step_name):
        for reporter in self.reporters:
            reporter.start_step(step_name)


class MyRichStatus:

    def __init__(self, text):
        self._text = text
        self.done = False
        self.failed = False
    
    def r(self):
        if self.done:
            if self.failed:
                return Text.from_markup(f":x: {self._text}")
            else:
                return Text.from_markup(f":white_heavy_check_mark: {self._text}")
        else:
            return Spinner("dots", self._text)
    
    def finish(self, failed=False):
        self.failed = failed
        self.done = True
        return self.r()

class RichReporter(AbstractReporter):

    def __init__(self):
        super(RichReporter, self).__init__()
        self.tree = Tree("Dugway")
        self.tree.hide_root = True
        self.display = Live(self.tree)
        self.services = []
        self.current_suite_tree: Tree = None
        self.current_suite_spinner: MyRichStatus = None
        self.current_case_tree: Tree = None
        self.current_case_spinner: MyRichStatus = None
        self.current_step_spinner: MyRichStatus = None
        self.current_step_tree: Tree = None

    def add_service(self, service_name, spinner='bouncingBar'):
        spinner = Spinner(spinner, service_name)
        if self.current_suite_tree is None:
            self.services.append(Spinner)
        else:
            self.current_suite_tree.add(spinner)

    def start_suite(self, suite_name):
        self.current_suite_spinner = MyRichStatus(f"Suite: {suite_name}")
        self.current_suite_tree = Tree(self.current_suite_spinner.r())
        for svc in self.services:
            self.current_suite_tree.add(svc)
        self.tree.add(self.current_suite_tree)
        self.display.start()

    def end_suite(self, result):
        self.current_suite_tree.label = self.current_suite_spinner.finish(failed=(not result))
        self.display.refresh()

    def start_case(self, case_name: str, number_of_cases: int|None=None):
        self.current_case_spinner = MyRichStatus(f"Case: {case_name}")
        self.current_case_tree = self.current_suite_tree.add(self.current_case_spinner.r())

    def end_case(self, result):
        self.current_case_tree.label = self.current_case_spinner.finish(failed=(not result))
        if result:
            self.current_case_tree.expanded = False
        self.display.refresh()

    def start_step(self, step_name):
        self.current_step_spinner = MyRichStatus(step_name)
        self.current_step_tree = self.current_case_tree.add(self.current_step_spinner.r())
    
    def end_step(self, result):
        self.current_step_tree.label = self.current_step_spinner.finish(failed=(not result))
        self.display.refresh()
    
class JunitReporter(AbstractReporter):

    def __init__(self, filename):
        super().__init__()
        self._filename = filename
        self.suite_name = ''
        self.test_cases = []

    def __del__(self):
        ts = junit_xml.TestSuite(self.suite_name, self.test_cases)
        try:
            with open(self._filename, 'w') as fp:
                junit_xml.TestSuite.to_file(fp, [ts])
        except NameError:
            pass
    
    def start_suite(self, suite_name):
        self.suite_name = suite_name
    
    def start_case(self, case_name):
        self.test_cases.append(junit_xml.TestCase(case_name))
    
    def end_case(self, result):
        if result is False:
            self.test_cases[-1].add_failure_info("Failed")
    
    def start_step(self, step_name):
        pass
