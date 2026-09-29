import pytest

from dugway.builtin_steps import AddService, ConvertFrom, Sleep, ValueSave
from dugway.capabilities import RawContentCapability, ValueCapability
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


def raw_source(runner, monkeypatch, body):
    raw = RawContentCapability(runner, {})
    raw.content = body.encode()
    monkeypatch.setattr(runner, "get_step", lambda step_id: SourceStep(runner, [raw]))


def test_json_step_without_schema(runner, monkeypatch):
    raw_source(runner, monkeypatch, '{"a": 1}')
    step = ConvertFrom(runner, {"type": "deserialize", "from": "src"})
    step.run()
    assert step.value_cap.get() == {"a": 1}


def test_json_step_schema_mismatch_is_an_expectation_failure(runner, monkeypatch):
    raw_source(runner, monkeypatch, '{"a": 1}')
    step = ConvertFrom(
        runner,
        {"type": "deserialize", "from": "src", "expect": {"json_schema": {"required": ["b"]}}},
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


def test_saved_variables_are_available_to_templates(runner_in_case, monkeypatch):
    value = ValueCapability(runner_in_case, {})
    value.set("abc")
    monkeypatch.setattr(runner_in_case, "get_step", lambda step_id: SourceStep(runner_in_case, [value]))
    ValueSave(runner_in_case, {"type": "save", "from": "src", "suite": "token", "case": "id"}).run()
    assert runner_in_case.template_eval("{{ suite.token }}/{{ case.id }}") == "abc/abc"


def test_variable_names_are_not_taken_by_dict_methods(runner_in_case):
    runner_in_case.get_suite().add_variable("items", "mine")
    assert runner_in_case.template_eval("{{ suite.items }}") == "mine"


def test_templates_render_before_any_case_runs(runner):
    assert runner.template_eval("[{{ suite.missing }}][{{ case.missing }}]") == "[][]"


def test_sleep_time_is_evaluated_when_run(runner_in_case, monkeypatch):
    slept = []
    monkeypatch.setattr("dugway.builtin_steps.sleep", slept.append)
    step = Sleep(runner_in_case, {"type": "sleep", "time": "{{ case.delay }}"})
    runner_in_case.get_suite().current_case.add_variable("delay", 3)
    step.run()
    assert slept == [3]


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
