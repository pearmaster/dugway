"""The help command and the type references it shows."""

import pytest
from typer.testing import CliRunner

from dugway import cli
from dugway.reference import Kind, UnknownType, describe, summaries

runner = CliRunner()


def options_by_name(kind, name):
    return {option.name: option for option in describe(kind, name).options}


def test_options_include_those_from_capabilities():
    options = options_by_name(Kind.steps, "mqtt_subscribe")
    assert options["service"].required == "yes"
    assert options["topic"].required == ""
    assert "filter.json_schema" in options
    assert "filter.publishProperties.contentType" in options


def test_options_merge_a_property_shared_by_capabilities():
    options = options_by_name(Kind.steps, "mqtt_message")
    assert {"expect.count", "expect.topic", "expect.json_schema"} <= options.keys()


def test_type_option_is_the_type_name():
    assert options_by_name(Kind.services, "http")["type"].type == '"http"'


def test_alternatives_are_reported_as_exclusive():
    ref = describe(Kind.steps, "jsonpath")
    assert ref.exclusive_groups == [["path", "pointer"]]
    options = {option.name: option for option in ref.options}
    assert options["path"].required == "one of"
    assert options["pointer"].required == "one of"


def test_required_options_are_listed_first():
    names = [option.name for option in describe(Kind.steps, "jsonpath").options]
    assert names[:4] == ["type", "from", "path", "pointer"]


def test_nested_options_follow_their_parent():
    names = [option.name for option in describe(Kind.services, "mqtt").options]
    parent = names.index("credentials")
    assert names[parent + 1 : parent + 3] == ["credentials.username", "credentials.password"]


def test_type_details_describe_types_and_values():
    options = options_by_name(Kind.steps, "mqtt_message")
    assert options["consume"].type == 'integer ≥ 0 or "all"'
    assert options["consume"].default == '"all"'
    assert options["timeoutSeconds"].type == "number or null"


@pytest.mark.parametrize("kind", list(Kind))
def test_every_builtin_type_is_documented(kind):
    for name, summary in summaries(kind).items():
        assert summary != "No description available.", name
        undocumented = [option.name for option in describe(kind, name).options if not option.description]
        assert undocumented == [], name


def test_describe_rejects_unknown_type():
    with pytest.raises(UnknownType):
        describe(Kind.steps, "bogus")


def test_help_shows_a_named_type():
    result = runner.invoke(cli.app, ["help", "steps", "jsonpath"])
    assert result.exit_code == 0
    assert "Finds values in the JSON" in result.output
    assert "give exactly one of: path, pointer" in result.output


def test_help_rejects_an_unknown_type():
    result = runner.invoke(cli.app, ["help", "steps", "bogus"])
    assert result.exit_code == 2
    assert "no step type named 'bogus'" in result.output


def test_help_lists_types_when_not_interactive():
    result = runner.invoke(cli.app, ["help"])
    assert result.exit_code == 0
    assert "Available services" in result.output
    assert "Available steps" in result.output


def test_help_lists_one_kind_when_given():
    result = runner.invoke(cli.app, ["help", "services"])
    assert result.exit_code == 0
    assert "Available services" in result.output
    assert "Available steps" not in result.output


class FakePrompts:
    """Answers questionary prompts from a list, recording the choices offered."""

    def __init__(self, monkeypatch, answers):
        self.answers = list(answers)
        self.offered = []
        monkeypatch.setattr(cli, "_is_interactive", lambda: True)
        monkeypatch.setattr(cli.questionary, "select", self.prompt)
        monkeypatch.setattr(cli.questionary, "confirm", self.prompt)

    def prompt(self, message, choices=None, **kwargs):
        self.offered.append([choice.value for choice in choices or []])
        answer = self.answers.pop(0)
        return type("Question", (), {"ask": lambda self: answer})()


def test_help_browses_to_a_type(monkeypatch):
    prompts = FakePrompts(monkeypatch, [Kind.services, "mqtt", False])
    result = runner.invoke(cli.app, ["help"])
    assert result.exit_code == 0
    assert prompts.offered[0] == [Kind.services, Kind.converters, Kind.steps]
    assert prompts.offered[1] == list(summaries(Kind.services))
    assert "An MQTT broker connection" in result.output


def test_help_browses_within_the_given_kind_until_done(monkeypatch):
    prompts = FakePrompts(monkeypatch, ["sleep", True, "save", False])
    result = runner.invoke(cli.app, ["help", "steps"])
    assert result.exit_code == 0
    assert prompts.offered[0] == list(summaries(Kind.steps))
    assert "Pauses the test case" in result.output
    assert "Saves the value" in result.output


def test_help_stops_when_a_menu_is_cancelled(monkeypatch):
    FakePrompts(monkeypatch, [Kind.steps, None])
    result = runner.invoke(cli.app, ["help"])
    assert result.exit_code == 0
    assert "Options" not in result.output


def test_reference_lists_the_capabilities_a_step_is_built_with():
    capabilities = {cap.name: cap.summary for cap in describe(Kind.steps, "deserialize").capabilities}
    assert list(capabilities) == ["FromStep", "JsonSchemaExpect", "Conversion", "Value", "MultiValue"]
    assert capabilities["FromStep"].startswith("Uses what an earlier step")


def test_help_shows_capabilities():
    result = runner.invoke(cli.app, ["help", "steps", "mqtt_subscribe"])
    assert result.exit_code == 0
    assert "Capabilities" in result.output
    assert "RawMultiContent" in result.output


def test_help_leaves_out_capabilities_for_types_without_any():
    result = runner.invoke(cli.app, ["help", "converters", "json"])
    assert result.exit_code == 0
    assert "Capabilities" not in result.output
