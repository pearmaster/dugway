
from abc import ABC, abstractmethod
from functools import partial
from io import StringIO

import junit_xml
from rich.tree import Tree
from rich.live import Live
from rich.spinner import Spinner
from rich.console import Console
from rich.text import Text
from rich.emoji import Emoji
from rich.progress_bar import ProgressBar
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax
from rich.traceback import Traceback
from rich import print
from protobuf_inspector.types import StandardParser as ProtobufParser

from .expectations import FailedTestStep, ExpectationFailure

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

    def step_info(self, title, data):
        ...

    def end_step(self, result: bool):
        ...

    def add_service(self, service_name):
        ...

    def step_failure(self, title, data):
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

    def __init__(self, status_type, text):
        self._text = text
        self._status_type = status_type
        self.done = False
        self.failed = False
    
    def r(self):
        if self.done:
            if self.failed:
                return Text.from_markup(f":x: [bold blue]{self._status_type}[/bold blue]: [red]{self._text}[/red]")
            else:
                return Text.from_markup(f":white_heavy_check_mark: [bold blue]{self._status_type}[/bold blue]: [green]{self._text}[/green]")
        else:
            return Spinner("dots", f"[bold blue]{self._status_type}[/bold blue]: {self._text}")
    
    def finish(self, failed=False):
        self.failed = failed
        self.done = True
        return self.r()
    

def create_rich_table(data:dict[str,str]) -> Table:
    table = Table()
    for k,v in data.items():
        table.add_row(k, v)
    return table

def create_rich_text(data:str, line_numbers=True):
    if '\n' in data:
        return Syntax(data, "text", line_numbers=line_numbers)
    elif inspectedpb := try_display_raw_protobuf(data):
        return Text(inspectedpb)
    return data

def create_rich_panel(title:str, data:str|dict[str,str]|list[str|dict[str,str]]|FailedTestStep|None=None, error_panel:bool=False) -> Panel|Text|Table:
    kwargs = dict()
    if error_panel:
        kwargs['style'] = "red"
    if data is None:
        return Text(title)
    else:
        if isinstance(data, str):
            return Panel(create_rich_text(data), title=title, width=80, **kwargs)
        elif isinstance(data, dict):
            table = create_rich_table(data)
        elif isinstance(data, list):
            return Panel(create_rich_text("\n".join([str(d) for d in data]), line_numbers=False), title=title, width=80, **kwargs)
        elif hasattr(data, "details"):
            return Panel(create_rich_text(data.details(), line_numbers=False), title=title, width=80, **kwargs)
        elif isinstance(data, Exception):
            return Panel(Traceback.from_exception(type(data), data, data.__traceback__))

def try_display_raw_protobuf(data) -> str|None:
    parser = ProtobufParser()
    f = StringIO(data)
    try:
        output = parser.parse_message(f, "message")
        return output
    except Exception:
        return None

class RichReporter(AbstractReporter):

    def __init__(self):
        super(RichReporter, self).__init__()
        self.tree = Tree("Dugway")
        self.tree.hide_root = True
        self.display = Live(self.tree, vertical_overflow="visible")
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
        self.current_suite_spinner = MyRichStatus("Suite", suite_name)
        self.current_suite_tree = Tree(self.current_suite_spinner.r())
        for svc in self.services:
            self.current_suite_tree.add(svc)
        self.tree.add(self.current_suite_tree)
        self.display.start()

    def end_suite(self, result):
        self.current_suite_tree.label = self.current_suite_spinner.finish(failed=(not result))
        self.display.refresh()

    def start_case(self, case_name: str, number_of_cases: int|None=None):
        self.current_case_spinner = MyRichStatus("Case", case_name)
        self.current_case_tree = self.current_suite_tree.add(self.current_case_spinner.r())

    def end_case(self, result):
        self.current_case_tree.label = self.current_case_spinner.finish(failed=(not result))
        if result:
            self.current_case_tree.expanded = False
        self.display.refresh()

    def start_step(self, step_name):
        self.current_step_spinner = MyRichStatus("Step", step_name)
        self.current_step_tree = self.current_case_tree.add(self.current_step_spinner.r())
    
    def step_info(self, title, data):
        self.current_step_tree.add(create_rich_panel(title, data))

    def step_failure(self, title, data=None):
        self.current_step_tree.add(create_rich_panel(title, data, error_panel=True))
        self.display.refresh()
        self.end_step(False)

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

if __name__ == '__main__':
    rr = RichReporter()
    rr.start_suite("This is the test suite")
    rr.start_case("This is the test case name")
    rr.start_step("Hello")
    rr.step_info("String", "This is some text")
    rr.step_info("Multiline", "Line one\nLine two\nLine three")
    #rr.step_info("List", ["Thing One", "Thing Two", "Thing Three"])
    rr.end_step(True)
    rr.start_step("Goodbye")
    rr.end_step(False)
    rr.end_case(False)
    rr.end_suite(False)