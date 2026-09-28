import pytest

from dugway.builtin_steps import AddService, ConvertToJson, ValueSave
from dugway.capabilities import TextContentCapability, ValueCapability
from dugway.expectations import ExpectationFailure, FailedTestStep
from dugway.reporter import NoOpReporter
from dugway.runner import DugwayRunner
from dugway.web import HttpService
from helpers import SourceStep


@pytest.fixture
def runner_in_case(tmp_path):
    """A runner whose suite is part way through running its only test case."""
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text("services: {}\ntestCases:\n  c:\n    steps:\n      - type: sleep\n        time: 0\n")
    runner = DugwayRunner(str(suite_file), NoOpReporter())
    suite = runner.get_suite()
    suite._current_case = dict(suite.iterate_test_cases())["c"]
    return runner


def text_source(runner, monkeypatch, body):
    text = TextContentCapability(runner, {})
    text.response_body = body
    monkeypatch.setattr(runner, "get_step", lambda step_id: SourceStep(runner, [text]))


def test_json_step_without_schema(runner, monkeypatch):
    text_source(runner, monkeypatch, '{"a": 1}')
    step = ConvertToJson(runner, {"type": "json", "from": "src"})
    step.run()
    assert step.json_content_cap.json_content == {"a": 1}


def test_json_step_schema_mismatch_is_an_expectation_failure(runner, monkeypatch):
    text_source(runner, monkeypatch, '{"a": 1}')
    step = ConvertToJson(
        runner,
        {"type": "json", "from": "src", "expect": {"json_schema": {"required": ["b"]}}},
    )
    with pytest.raises(ExpectationFailure, match="did not match the JSON Schema"):
        step.run()


def test_save_step_saves_suite_and_case_variables(runner_in_case, monkeypatch):
    value = ValueCapability(runner_in_case, {})
    value.set(42)
    monkeypatch.setattr(runner_in_case, "get_step", lambda step_id: SourceStep(runner_in_case, [value]))
    ValueSave(runner_in_case, {"type": "save", "from": "src", "suite": "s", "case": "c"}).run()
    suite = runner_in_case.get_suite()
    assert suite._variables["s"] == 42
    assert suite.current_case._variables["c"] == 42


def test_save_step_from_step_without_value(runner_in_case, monkeypatch):
    monkeypatch.setattr(runner_in_case, "get_step", lambda step_id: SourceStep(runner_in_case, []))
    step = ValueSave(runner_in_case, {"type": "save", "from": "src", "case": "c"})
    with pytest.raises(FailedTestStep, match="does not provide a value"):
        step.run()


def test_service_step_adds_and_sets_up_service(runner):
    step = AddService(
        runner,
        {
            "type": "service",
            "name": "api",
            "config": {"type": "http", "hostname": "example.com"},
        },
    )
    step.run()
    assert isinstance(runner.get_service("api"), HttpService)
