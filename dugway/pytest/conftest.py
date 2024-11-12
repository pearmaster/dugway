import os
import pytest

class DugwayYamlFile(pytest.File):

    def collect(self):
        print("****Collecting Tests")
        pass

def pytest_runtest_setup(item):
    print("******ff**")

def pytest_collect_file(parent, path):
    print("********")
    if path.ext == ".dugway.yaml" or path.ext == ".dugway.yml":
        return DugwayYamlFile.from_parent(parent, fspath=path)

