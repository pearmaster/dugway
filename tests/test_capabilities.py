from types import SimpleNamespace

import pytest

from dugway.builtin_steps import ConvertToJson, JsonPath
from dugway.capabilities import (
    JsonMultiContentCapability,
    MultiValueCapability,
    TextContentCapability,
    TextMultiContentCapability,
)
from dugway.expectations import ExpectationFailure, FailedTestStep
from dugway.mqtt import MqttMessage, MqttSubscribe, MqttService

from helpers import SourceStep


@pytest.mark.parametrize(
    "cap_class", [TextMultiContentCapability, JsonMultiContentCapability]
)
def test_multi_content_get_or_none_returns_queued_items(runner, cap_class):
    cap = cap_class(runner, {})
    cap.add_content("first")
    cap.add_content("second")
    assert cap.get_or_none() == "first"
    assert cap.get_or_none() == "second"
    assert cap.get_or_none() is None


def test_multi_value_get_or_none_returns_queued_items(runner):
    cap = MultiValueCapability(runner, {})
    cap.add_content(1)
    assert cap.get_or_none() == 1
    assert cap.get_or_none() is None


def test_jsonpath_reads_from_multi_json_content(runner, monkeypatch):
    multi = JsonMultiContentCapability(runner, {})
    multi.add_content({"a": 1})
    multi.add_content({"a": 2})
    monkeypatch.setattr(runner, "get_step", lambda step_id: SourceStep(runner, [multi]))
    step = JsonPath(
        runner, {"type": "jsonpath", "from": "src", "path": "$.a", "minimum": 2}
    )
    step.run()
    assert step.value_cap.get() == 1


def test_jsonpath_enforces_maximum(runner, monkeypatch):
    multi = JsonMultiContentCapability(runner, {})
    multi.add_content({"a": 1})
    multi.add_content({"a": 2})
    monkeypatch.setattr(runner, "get_step", lambda step_id: SourceStep(runner, [multi]))
    step = JsonPath(
        runner, {"type": "jsonpath", "from": "src", "path": "$.a", "maximum": 1}
    )
    with pytest.raises(FailedTestStep, match="Found 2 matches but only 1 are allowed"):
        step.run()


def test_jsonpath_enforces_minimum(runner, monkeypatch):
    multi = JsonMultiContentCapability(runner, {})
    multi.add_content({"a": 1})
    monkeypatch.setattr(runner, "get_step", lambda step_id: SourceStep(runner, [multi]))
    step = JsonPath(
        runner, {"type": "jsonpath", "from": "src", "path": "$.a", "minimum": 2}
    )
    with pytest.raises(
        FailedTestStep, match="Only found 1 matches but 2 were required"
    ):
        step.run()


def test_mqtt_message_checks_messages_from_subscription(runner, monkeypatch):
    sub = MqttSubscribe(
        runner, {"type": "mqtt_subscribe", "service": "broker", "topic": "t"}
    )
    sub._receive_message(
        None, None, SimpleNamespace(topic="t", payload=b'"hi"', properties=None)
    )
    monkeypatch.setattr(runner, "get_step", lambda step_id: sub)
    check = MqttMessage(
        runner,
        {
            "type": "mqtt_message",
            "from": "sub",
            "timeoutSeconds": 1,
            "expect": {"count": 1, "topic": "t", "json_schema": {"const": "bye"}},
        },
    )
    with pytest.raises(ExpectationFailure):
        check.run()


def test_mqtt_message_checks_json_content(runner, monkeypatch):
    text = TextContentCapability(runner, {})
    text.response_body = '{"a": 1}'
    source = SourceStep(runner, [text])
    to_json = ConvertToJson(
        runner,
        {"type": "json", "from": "src", "expect": {"json_schema": {"type": "object"}}},
    )
    monkeypatch.setattr(runner, "get_step", lambda step_id: source)
    to_json.run()

    monkeypatch.setattr(runner, "get_step", lambda step_id: to_json)
    check = MqttMessage(
        runner,
        {
            "type": "mqtt_message",
            "from": "to_json",
            "expect": {"json_schema": {"required": ["b"]}},
        },
    )
    with pytest.raises(ExpectationFailure):
        check.run()


def test_template_eval_bool(runner):
    assert runner.template_eval(True) is True
    assert runner.template_eval(False) is False


def test_mqtt_connect_properties_are_applied(runner):
    service = MqttService(
        runner,
        {
            "type": "mqtt",
            "hostname": "localhost",
            "protocol": 5,
            "connectProperties": {"sessionExpiryInterval": 30, "receiveMaximum": 10},
        },
    )
    calls = []
    service.client = SimpleNamespace(
        connect=lambda *a, **kw: calls.append(kw), loop_start=lambda: None
    )
    service.setup()
    props = calls[0]["properties"]
    assert props.SessionExpiryInterval == 30
    assert props.ReceiveMaximum == 10
