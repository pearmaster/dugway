import pytest

from dugway.reporter import NoOpReporter
from dugway.runner import DugwayRunner

pytest_plugins = ["pytester"]


@pytest.fixture
def runner(tmp_path):
    """A runner for an empty suite, for constructing steps and services directly."""
    suite_file = tmp_path / "empty.yaml"
    suite_file.write_text("services: {}\ntestCases: {}\n")
    return DugwayRunner(str(suite_file), NoOpReporter())
