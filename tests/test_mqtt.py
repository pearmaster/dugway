from types import SimpleNamespace
import warnings

import pytest
import paho.mqtt.properties as props
from paho.mqtt.packettypes import PacketTypes

from dugway.expectations import ExpectationFailure, InvalidTestConfig
from dugway.mqtt import MqttMessage, MqttPublish, MqttService, MqttSubscribe


def mqtt_message(topic="t", payload=b'"hi"', properties=None):
    return SimpleNamespace(topic=topic, payload=payload, properties=properties)


def test_service_uses_paho_v2_callback_api(runner):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        MqttService(runner, {"type": "mqtt", "hostname": "localhost"})


def test_v5_clean_session_is_sent_as_clean_start(runner):
    service = MqttService(
        runner,
        {"type": "mqtt", "hostname": "localhost", "protocol": 5, "cleanSession": False},
    )
    calls = []
    service.client = SimpleNamespace(
        connect=lambda *a, **kw: calls.append(kw), loop_start=lambda: None
    )
    service.setup()
    assert calls[0]["clean_start"] is False


def test_credentials_config_is_accepted(runner):
    MqttService(
        runner,
        {
            "type": "mqtt",
            "hostname": "localhost",
            "credentials": {"username": "u", "password": "p"},
        },
    )


def test_invalid_protocol_is_rejected(runner):
    with pytest.raises(InvalidTestConfig):
        MqttService(runner, {"type": "mqtt", "hostname": "localhost", "protocol": 4})


@pytest.mark.parametrize(
    "config, payload",
    [
        ({"json": {"a": 1}}, '{"a": 1}'),
        ({"json": 0}, "0"),
        ({"json": None}, "null"),
        ({"nullPayload": True}, None),
    ],
)
def test_publish_payload(runner, config, payload):
    step = MqttPublish(
        runner, {"type": "mqtt_publish", "service": "s", "topic": "t", **config}
    )
    assert step._payload == payload


def test_publish_without_payload_is_rejected(runner):
    with pytest.raises(InvalidTestConfig):
        MqttPublish(runner, {"type": "mqtt_publish", "service": "s", "topic": "t"})


def subscription(runner, monkeypatch, **config):
    sub = MqttSubscribe(
        runner, {"type": "mqtt_subscribe", "service": "broker", "topic": "t", **config}
    )
    monkeypatch.setattr(runner, "get_step", lambda step_id: sub)
    return sub


def test_non_json_message_fails_the_consuming_step(runner, monkeypatch):
    sub = subscription(runner, monkeypatch)
    sub._receive_message(None, None, mqtt_message(payload=b"not json"))
    check = MqttMessage(runner, {"type": "mqtt_message", "from": "sub"})
    with pytest.raises(ExpectationFailure, match="was not JSON"):
        check.run()


def test_non_utf8_message_fails_the_consuming_step(runner, monkeypatch):
    sub = subscription(runner, monkeypatch)
    sub._receive_message(None, None, mqtt_message(payload=b"\xff\xfe"))
    check = MqttMessage(runner, {"type": "mqtt_message", "from": "sub"})
    with pytest.raises(ExpectationFailure):
        check.run()


def test_property_filter_skips_messages_without_properties(runner, monkeypatch):
    sub = subscription(
        runner, monkeypatch, filter={"publishProperties": {"correlationData": "1234"}}
    )
    sub._receive_message(None, None, mqtt_message(properties=None))
    sub._receive_message(
        None, None, mqtt_message(properties=props.Properties(PacketTypes.PUBLISH))
    )
    matching = props.Properties(PacketTypes.PUBLISH)
    matching.CorrelationData = b"1234"
    sub._receive_message(None, None, mqtt_message(properties=matching))
    assert sub._json_multi.errors == []
    assert sub._json_multi.count == 1


def test_property_filter_schema_is_validated(runner):
    with pytest.raises(InvalidTestConfig):
        MqttSubscribe(
            runner,
            {
                "type": "mqtt_subscribe",
                "service": "broker",
                "topic": "t",
                "filter": {"publishProperties": {"payloadFormatIndicator": 7}},
            },
        )


def test_message_count_without_timeout(runner, monkeypatch):
    sub = subscription(runner, monkeypatch)
    sub._receive_message(None, None, mqtt_message())
    check = MqttMessage(
        runner, {"type": "mqtt_message", "from": "sub", "expect": {"count": 1}}
    )
    check.run()


def test_consume_leaves_remaining_messages(runner, monkeypatch):
    sub = subscription(runner, monkeypatch)
    sub._receive_message(None, None, mqtt_message())
    sub._receive_message(None, None, mqtt_message())
    MqttMessage(runner, {"type": "mqtt_message", "from": "sub", "consume": 1}).run()
    assert sub._json_multi.count == 1
