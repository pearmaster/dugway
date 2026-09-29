import warnings
from types import SimpleNamespace

import paho.mqtt.properties as props
import pytest
from paho.mqtt.packettypes import PacketTypes

from dugway import mqtt
from dugway.builtin_steps import ConvertFrom
from dugway.expectations import ExpectationFailure, FailedTestStep, InvalidTestConfig
from dugway.mqtt import MqttMessage, MqttPublish, MqttService, MqttSubscribe
from helpers import FakeMqttClient


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
    service.client = FakeMqttClient(service)
    service.setup()
    calls = service.client.connects
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
        ({"json": {"a": 1}}, b'{"a": 1}'),
        ({"json": 0}, b"0"),
        ({"json": None}, b"null"),
        ({"nullPayload": True}, None),
    ],
)
def test_publish_payload(runner, config, payload):
    step = MqttPublish(runner, {"type": "mqtt_publish", "service": "s", "topic": "t", **config})
    assert step.outgoing_message().payload == payload


def test_publish_without_payload_is_rejected(runner):
    with pytest.raises(InvalidTestConfig):
        MqttPublish(runner, {"type": "mqtt_publish", "service": "s", "topic": "t"})


def subscription(runner, monkeypatch, **config):
    sub = MqttSubscribe(runner, {"type": "mqtt_subscribe", "service": "broker", "topic": "t", **config})
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
    sub = subscription(runner, monkeypatch, filter={"publishProperties": {"correlationData": "1234"}})
    sub._receive_message(None, None, mqtt_message(properties=None))
    sub._receive_message(None, None, mqtt_message(properties=props.Properties(PacketTypes.PUBLISH)))
    matching = props.Properties(PacketTypes.PUBLISH)
    matching.CorrelationData = b"1234"
    sub._receive_message(None, None, mqtt_message(properties=matching))
    assert sub._raw_multi.errors == []
    assert sub._raw_multi.count == 1


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
    check = MqttMessage(runner, {"type": "mqtt_message", "from": "sub", "expect": {"count": 1}})
    check.run()


def test_consume_leaves_remaining_messages(runner, monkeypatch):
    sub = subscription(runner, monkeypatch)
    sub._receive_message(None, None, mqtt_message())
    sub._receive_message(None, None, mqtt_message())
    MqttMessage(runner, {"type": "mqtt_message", "from": "sub", "consume": 1}).run()
    assert sub._raw_multi.count == 1


def mqtt_service(runner, monkeypatch, **fake_client_kwargs):
    monkeypatch.setattr(mqtt, "BROKER_ACK_TIMEOUT_SECONDS", 0.1)
    service = MqttService(runner, {"type": "mqtt", "hostname": "localhost"})
    service.client = FakeMqttClient(service, **fake_client_kwargs)
    return service


def test_setup_fails_when_broker_refuses_connection(runner, monkeypatch):
    service = mqtt_service(runner, monkeypatch, connack="Not authorized")
    with pytest.raises(ConnectionError, match="refused"):
        service.setup()


def test_setup_fails_when_broker_never_acknowledges(runner, monkeypatch):
    service = mqtt_service(runner, monkeypatch, connack=None)
    with pytest.raises(ConnectionError, match="did not acknowledge"):
        service.setup()


def test_subscribe_waits_for_acknowledgement(runner, monkeypatch):
    service = mqtt_service(runner, monkeypatch)
    service.subscribe("t", 0, lambda *a: None)
    assert service.client.subscribes == ["t"]


def test_subscribe_fails_when_broker_refuses(runner, monkeypatch):
    service = mqtt_service(runner, monkeypatch, suback="Not authorized")
    with pytest.raises(FailedTestStep, match="refused"):
        service.subscribe("t", 0, lambda *a: None)


def test_subscribe_fails_when_broker_never_acknowledges(runner, monkeypatch):
    service = mqtt_service(runner, monkeypatch, suback=None)
    with pytest.raises(FailedTestStep, match="did not acknowledge"):
        service.subscribe("t", 0, lambda *a: None)


def test_user_properties_are_published(runner, monkeypatch):
    service = mqtt_service(runner, monkeypatch)
    service.is_v5 = True
    monkeypatch.setattr(runner, "get_service", lambda name: service)
    MqttPublish(
        runner,
        {
            "type": "mqtt_publish",
            "service": "s",
            "topic": "t",
            "json": 1,
            "publishProperties": {"userProperties": {"a": "1", "b": "2"}},
        },
    ).run()
    assert service.client.publishes[-1]["properties"].UserProperty == [("a", "1"), ("b", "2")]


def test_user_property_filter(runner, monkeypatch):
    sub = subscription(runner, monkeypatch, filter={"publishProperties": {"userProperties": {"a": "1"}}})
    for user_props in ([("a", "2")], [("b", "1")], [("a", "1"), ("b", "9")]):
        received = props.Properties(PacketTypes.PUBLISH)
        received.UserProperty = user_props
        sub._receive_message(None, None, mqtt_message(properties=received))
    assert sub._raw_multi.count == 1


def test_messages_are_kept_as_received(runner, monkeypatch):
    sub = subscription(runner, monkeypatch)
    sub._receive_message(None, None, mqtt_message(topic="t/a", payload=b"\xff\xfe"))
    content = sub._raw_multi.get_content()
    assert (content.content, content.properties) == (b"\xff\xfe", {"topic": "t/a", "contentType": None})
    assert sub._raw_multi.errors == []


def test_json_step_parses_the_messages(runner, monkeypatch):
    sub = subscription(runner, monkeypatch)
    sub._receive_message(None, None, mqtt_message(payload=b'{"a": 1}'))
    step = ConvertFrom(runner, {"type": "deserialize", "from": "sub"})
    step.run()
    assert step.multi_value_cap.get() == {"a": 1}


def test_only_consumed_messages_must_be_json(runner, monkeypatch):
    sub = subscription(runner, monkeypatch)
    sub._receive_message(None, None, mqtt_message())
    sub._receive_message(None, None, mqtt_message(payload=b"not json"))
    MqttMessage(runner, {"type": "mqtt_message", "from": "sub", "consume": 1}).run()
    assert sub._raw_multi.get() == b"not json"
