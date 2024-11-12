import os
import pytest
from dugway.runner import TestRunner

class DugwayYamlItem(pytest.Item):
    def __init__(self, name, parent, test_data):
        super().__init__(name, parent)
        self.test_data = test_data

    def runtest(self):
        # Your code to run the test goes here.
        pass

    def repr_failure(self, excinfo):
        # Your code to format the failure message goes here.
        pass

    def reportinfo(self):
        # Your code to format the report message goes here.
        pass

class DugwayYamlFile(pytest.File):

    def collect(self):
        tr = TestRunner(str(self.fspath))
        cases = tr._suite._cases
        for case_name, case in cases.items():
            yield DugwayYamlItem.from_parent(self, name=case_name, test_data=case)

def pytest_collect_file(parent, path):
    if os.path.basename(path).endswith(".dugway.yaml") or os.path.basename(path).endswith(".dugway.yml"):
        return DugwayYamlFile.from_parent(parent, fspath=path)

