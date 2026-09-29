import json
from pathlib import Path
from types import SimpleNamespace

import paho.mqtt.properties as props
import pytest
from paho.mqtt.packettypes import PacketTypes

from dugway import mqtt
from dugway.asyncapi import AsyncApiPublish, AsyncApiService, AsyncApiSubscribe
from dugway.expectations import ExpectationFailure, FailedTestStep, InvalidTestConfig
from dugway.mqtt import MqttMessage
from dugway.reporter import NoOpReporter
from dugway.runner import DugwayRunner
from helpers import FakeMqttClient

SPEC = str(Path(__file__).parent / "devices.asyncapi.yaml")

ONLINE = {"online": True, "since": 100}

V2_SPEC = {
    "asyncapi": "2.6.0",
    "info": {"title": "t", "version": "1"},
    "servers": {"local": {"url": "mqtt://localhost:1884", "protocol": "mqtt", "protocolVersion": "3.1.1"}},
    "channels": {
        "sensors/{sensorId}/reading": {
            "parameters": {"sensorId": {"schema": {"type": "string", "enum": ["a", "b"]}}},
            "publish": {
                "operationId": "sendReading",
                "message": {
                    "oneOf": [
                        {"messageId": "celsius", "payload": {"type": "object", "required": ["c"]}},
                        {"messageId": "fahrenheit", "payload": {"type": "object", "required": ["f"]}},
                    ]
                },
            },
            "subscribe": {"operationId": "receiveReading", "message": {"payload": {"type": "number"}}},
        },
    },
}


@pytest.fixture
def service(runner, monkeypatch):
    def make(spec=SPEC, **config):
        monkeypatch.setattr(mqtt, "BROKER_ACK_TIMEOUT_SECONDS", 0.1)
        service = AsyncApiService(runner, {"type": "asyncapi", "spec": spec, **config})
        service.client = FakeMqttClient(service)
        monkeypatch.setattr(runner, "get_service", lambda name: service)
        return service

    return make


@pytest.fixture
def broker(service):
    return service()


@pytest.fixture
def v2_broker(service, tmp_path):
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(V2_SPEC))
    return service(str(path))


def publish(runner, operation_id, **config):
    config.setdefault("json", {"at": 1})
    step = AsyncApiPublish(runner, {"type": "asyncapi_publish", "service": "b", "operationId": operation_id, **config})
    step.run()


def subscribe(runner, monkeypatch, operation_id, **config):
    step = AsyncApiSubscribe(
        runner, {"type": "asyncapi_subscribe", "service": "b", "operationId": operation_id, **config}
    )
    step.run()
    monkeypatch.setattr(runner, "get_step", lambda step_id: step)
    return step


def receive(step, payload, topic="devices/d1/status"):
    step._receive_message(
        None, None, SimpleNamespace(topic=topic, payload=json.dumps(payload).encode(), properties=None)
    )


def check(runner, **config):
    MqttMessage(runner, {"type": "mqtt_message", "from": "sub", **config}).run()


def test_broker_options_come_from_the_documents_mqtt_server(broker):
    assert broker._config["hostname"] == "broker.example.com"
    assert broker._config["port"] == 8883
    assert broker._config["tls"] is True
    assert broker.is_v5


def test_given_options_take_precedence_over_the_server(service):
    broker = service(hostname="localhost", port=1883, tls=False)
    assert (broker._config["hostname"], broker._config["port"], broker._config["tls"]) == ("localhost", 1883, False)


def test_v2_server_url_and_protocol_version(v2_broker):
    assert v2_broker._config["hostname"] == "localhost"
    assert v2_broker._config["port"] == 1884
    assert v2_broker._config["protocol"] == 3.11


def test_non_mqtt_server_is_rejected(service):
    with pytest.raises(InvalidTestConfig, match="only MQTT is supported"):
        service(server="docs")


def test_unknown_server_is_rejected(service):
    with pytest.raises(InvalidTestConfig, match="no server named 'nope'"):
        service(server="nope")


def test_non_asyncapi_document_is_rejected(runner, tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"asyncapi": "1.2.0"}))
    with pytest.raises(InvalidTestConfig, match="not an AsyncAPI 2 or 3 document"):
        AsyncApiService(runner, {"type": "asyncapi", "spec": str(path)})


def test_publish_fills_in_the_channel_address(runner, broker):
    publish(runner, "sendCommand", parameters={"deviceId": "d1", "kind": "update"}, json={"at": 5, "note": None})
    sent = broker.client.publishes[-1]
    assert sent["topic"] == "devices/d1/commands/update"
    assert json.loads(sent["payload"]) == {"at": 5, "note": None}


def test_publish_uses_parameter_defaults(runner, broker):
    publish(runner, "sendCommand", parameters={"deviceId": "d1"})
    assert broker.client.publishes[-1]["topic"] == "devices/d1/commands/reboot"


def test_publish_evaluates_templates(runner, broker):
    runner.get_suite().add_variable("device", "d9")
    runner.get_suite().add_variable("at", "7")
    publish(
        runner, "sendCommand", parameters={"deviceId": "{{ suite.device }}"}, json={"at": 1, "note": "{{ suite.at }}"}
    )
    sent = broker.client.publishes[-1]
    assert sent["topic"] == "devices/d9/commands/reboot"
    assert json.loads(sent["payload"])["note"] == "7"


@pytest.mark.parametrize(
    "config, error",
    [
        ({"parameters": {}}, "requires the parameter 'deviceId'"),
        ({"parameters": {"deviceId": "d1", "colour": "red"}}, "no parameter named 'colour'"),
        ({"parameters": {"deviceId": "d1", "kind": "explode"}}, "not one of the documented values"),
        ({"parameters": {"deviceId": "a/b"}}, "can't contain"),
        ({"parameters": {"deviceId": "d1"}, "json": {"at": "soon"}}, "does not match any message"),
    ],
)
def test_publish_fails_before_sending(runner, broker, config, error):
    with pytest.raises(FailedTestStep, match=error):
        publish(runner, "sendCommand", **config)
    assert broker.client.publishes == []


def test_null_payload_is_published_without_checking(runner, broker):
    # A JSON null is a payload, and is checked, but an empty message is not
    with pytest.raises(FailedTestStep, match="does not match any message of operation"):
        publish(runner, "sendCommand", parameters={"deviceId": "d1"}, json=None)
    step = AsyncApiPublish(
        runner,
        {
            "type": "asyncapi_publish",
            "service": "b",
            "operationId": "sendCommand",
            "parameters": {"deviceId": "d1"},
            "nullPayload": True,
        },
    )
    step.run()
    assert broker.client.publishes[-1]["payload"] is None


def test_unknown_operation_fails(runner, broker):
    with pytest.raises(FailedTestStep, match="no operation with operationId 'fly'"):
        publish(runner, "fly")


def test_channel_without_address_fails(runner, broker):
    with pytest.raises(FailedTestStep, match="has no address"):
        publish(runner, "sendAnything")


def test_v2_payload_may_be_any_of_the_messages(runner, v2_broker):
    publish(runner, "sendReading", parameters={"sensorId": "a"}, json={"c": 20})
    publish(runner, "sendReading", parameters={"sensorId": "b"}, json={"f": 68})
    assert [p["topic"] for p in v2_broker.client.publishes] == ["sensors/a/reading", "sensors/b/reading"]
    with pytest.raises(FailedTestStep, match="celsius: .*; fahrenheit: "):
        publish(runner, "sendReading", parameters={"sensorId": "a"}, json={"k": 293})


def test_v2_parameter_enum_is_in_its_schema(runner, v2_broker):
    with pytest.raises(FailedTestStep, match="not one of the documented values"):
        publish(runner, "sendReading", parameters={"sensorId": "z"}, json={"c": 1})


def test_message_can_be_chosen_by_name(runner, v2_broker):
    publish(runner, "sendReading", parameters={"sensorId": "a"}, message="celsius", json={"c": 1})
    with pytest.raises(FailedTestStep, match="does not match"):
        publish(runner, "sendReading", parameters={"sensorId": "a"}, message="celsius", json={"f": 1})
    with pytest.raises(FailedTestStep, match="no message named 'kelvin'. It has: celsius, fahrenheit"):
        publish(runner, "sendReading", parameters={"sensorId": "a"}, message="kelvin", json={"c": 1})


def test_subscribe_uses_wildcards_for_parameters_not_given(runner, monkeypatch, broker):
    subscribe(runner, monkeypatch, "receiveStatus")
    subscribe(runner, monkeypatch, "receiveStatus", parameters={"deviceId": "d1"})
    assert broker.client.subscribes == ["devices/+/status", "devices/d1/status"]


def test_received_messages_are_checked_against_the_document(runner, monkeypatch, broker):
    sub = subscribe(runner, monkeypatch, "receiveStatus")
    receive(sub, ONLINE)
    receive(sub, {"online": False})
    assert [c.properties["message"] for c in list(sub._raw_multi._messages.queue)] == ["online", "offline"]
    check(runner, expect={"count": 2})


def test_message_not_in_the_document_fails_the_consuming_step(runner, monkeypatch, broker):
    sub = subscribe(runner, monkeypatch, "receiveStatus")
    receive(sub, ONLINE)
    receive(sub, {"online": "maybe"})
    with pytest.raises(
        ExpectationFailure, match="does not match operation 'receiveStatus'.*matches none of its messages"
    ):
        check(runner)


def test_operation_messages_narrow_the_channels(runner, monkeypatch, broker):
    sub = subscribe(runner, monkeypatch, "onlineOnly")
    receive(sub, {"online": False})
    with pytest.raises(ExpectationFailure, match="online: "):
        check(runner)


def test_document_checks_come_before_expectations(runner, monkeypatch, broker):
    # The expected schema is met, but the document's is not
    sub = subscribe(runner, monkeypatch, "receiveStatus")
    receive(sub, {"online": True})
    with pytest.raises(ExpectationFailure, match="AsyncAPI document"):
        check(runner, expect={"json_schema": {"type": "object"}})


def test_expectations_are_checked_after_the_document(runner, monkeypatch, broker):
    sub = subscribe(runner, monkeypatch, "receiveStatus")
    receive(sub, ONLINE)
    with pytest.raises(ExpectationFailure, match="did not match the JSON Schema"):
        check(runner, expect={"json_schema": {"properties": {"since": {"const": 1}}}})


def test_filtered_out_messages_are_not_checked(runner, monkeypatch, broker):
    sub = subscribe(runner, monkeypatch, "receiveStatus", filter={"json_schema": {"required": ["since"]}})
    receive(sub, {"online": "not checked"})
    receive(sub, ONLINE)
    check(runner, expect={"count": 1})


def test_unknown_message_name_fails_the_subscribe_step(runner, monkeypatch, broker):
    with pytest.raises(FailedTestStep, match="no message named 'rebooting'"):
        subscribe(runner, monkeypatch, "receiveStatus", message="rebooting")


def test_topic_is_not_accepted(runner):
    with pytest.raises(InvalidTestConfig):
        AsyncApiSubscribe(runner, {"type": "asyncapi_subscribe", "service": "b", "topic": "t"})


def test_spec_path_is_relative_to_the_suite_file(tmp_path, monkeypatch):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "v2.json").write_text(json.dumps(V2_SPEC))
    suite = tmp_path / "suite.yaml"
    suite.write_text("services:\n  b:\n    type: asyncapi\n    spec: specs/v2.json\ntestCases: {}\n")
    runner = DugwayRunner(str(suite), NoOpReporter())
    assert runner.get_service("b")._config["port"] == 1884


# Directions


def test_publishing_a_receive_operation_fails(runner, broker):
    with pytest.raises(FailedTestStep, match="is a 'receive' operation .* not published with asyncapi_publish"):
        publish(runner, "receiveStatus", parameters={"deviceId": "d1"}, json=ONLINE)
    assert broker.client.publishes == []


def test_subscribing_to_a_send_operation_fails(runner, monkeypatch, broker):
    with pytest.raises(FailedTestStep, match="is a 'send' operation .* not subscribed to with asyncapi_subscribe"):
        subscribe(runner, monkeypatch, "sendCommand")
    assert broker.client.subscribes == []


def test_v2_directions_are_the_documents_words(runner, monkeypatch, v2_broker):
    subscribe(runner, monkeypatch, "receiveReading")
    assert v2_broker.client.subscribes == ["sensors/+/reading"]
    with pytest.raises(FailedTestStep, match="is a 'subscribe' operation"):
        publish(runner, "receiveReading", parameters={"sensorId": "a"}, json=1)
    with pytest.raises(FailedTestStep, match="is a 'publish' operation"):
        subscribe(runner, monkeypatch, "sendReading")


# Server bindings


def test_server_binding_gives_connection_options(broker):
    assert broker._config["clientId"] == "devices-tester"
    assert broker._config["keepAlive"] == 30
    assert broker._config["connectProperties"] == {"sessionExpiryInterval": 120}


def test_given_connection_options_take_precedence_over_the_binding(service):
    broker = service(clientId="me", connectProperties={"maximumPacketSize": 1000})
    assert broker._config["clientId"] == "me"
    assert broker._config["connectProperties"] == {"sessionExpiryInterval": 120, "maximumPacketSize": 1000}


def test_server_binding_sets_the_last_will(runner, monkeypatch):
    wills = []
    monkeypatch.setattr(mqtt.mqtt_client.Client, "will_set", lambda self, *args, **kwargs: wills.append((args, kwargs)))
    AsyncApiService(runner, {"type": "asyncapi", "spec": SPEC})
    assert wills == [(("devices/tester/status",), {"payload": "gone", "qos": 1, "retain": True})]


# Headers and operation and message bindings, when publishing

ALERT_PROPERTIES = {"userProperties": {"source": "tester"}, "correlationData": "req-1"}


def published_properties(broker):
    sent = broker.client.publishes[-1]["properties"]
    return {
        "PayloadFormatIndicator": sent.PayloadFormatIndicator,
        "ContentType": sent.ContentType,
        "ResponseTopic": sent.ResponseTopic,
        "MessageExpiryInterval": sent.MessageExpiryInterval,
        "CorrelationData": sent.CorrelationData,
        "UserProperty": sent.UserProperty,
    }


def test_publish_fills_in_bindings(runner, broker):
    publish(runner, "sendAlert", json={"level": "high"}, publishProperties=ALERT_PROPERTIES)
    sent = broker.client.publishes[-1]
    assert (sent["qos"], sent["retain"]) == (1, False)
    assert published_properties(broker) == {
        "PayloadFormatIndicator": 1,
        "ContentType": "application/json",
        "ResponseTopic": "alerts/replies",
        "MessageExpiryInterval": 60,
        "CorrelationData": b"req-1",
        "UserProperty": [("source", "tester")],
    }


@pytest.mark.parametrize(
    "config, error",
    [
        ({"publishProperties": {"correlationData": "req-1"}}, "headers \\(user properties\\) .*source"),
        ({"publishProperties": {"userProperties": {"source": "t"}, "correlationData": "x"}}, "correlationData"),
        ({"qos": 2, "publishProperties": ALERT_PROPERTIES}, "gives qos 2, but the MQTT binding"),
        ({"retain": True, "publishProperties": ALERT_PROPERTIES}, "gives retain true"),
        ({"publishProperties": {**ALERT_PROPERTIES, "contentType": "text/plain"}}, 'contentType "text/plain"'),
        ({"publishProperties": {**ALERT_PROPERTIES, "messageExpiryInterval": 5}}, "messageExpiryInterval 5"),
        ({"publishProperties": {**ALERT_PROPERTIES, "responseTopic": "elsewhere"}}, 'responseTopic "elsewhere"'),
    ],
)
def test_publish_that_breaks_headers_or_bindings_fails_before_sending(runner, broker, config, error):
    with pytest.raises(FailedTestStep, match=error):
        publish(runner, "sendAlert", json={"level": "high"}, **config)
    assert broker.client.publishes == []


def test_headers_and_properties_are_not_checked_before_mqtt_5(runner, service):
    broker = service(protocol=3.11)
    publish(runner, "sendAlert", json={"level": "high"})
    sent = broker.client.publishes[-1]
    assert sent["qos"] == 1
    assert "properties" not in sent


# Headers and operation and message bindings, when receiving


def alert(qos=1, **overrides):
    properties = props.Properties(PacketTypes.PUBLISH)
    values = {
        "PayloadFormatIndicator": 1,
        "ContentType": "application/json",
        "ResponseTopic": "alerts/replies",
        "CorrelationData": b"req-7",
        "MessageExpiryInterval": 59,
        "UserProperty": [("source", "device")],
        **overrides,
    }
    for name, value in values.items():
        if value is not None:
            setattr(properties, name, value)
    return SimpleNamespace(topic="alerts", payload=b'{"level": "low"}', properties=properties, qos=qos)


def test_subscription_qos_defaults_to_the_binding(runner, monkeypatch, broker):
    sub = subscribe(runner, monkeypatch, "receiveAlert")
    assert sub.subscription_qos() == 1
    with pytest.raises(FailedTestStep, match="below the qos 1"):
        subscribe(runner, monkeypatch, "receiveAlert", qos=0)


def test_message_matching_headers_and_bindings_is_kept(runner, monkeypatch, broker):
    sub = subscribe(runner, monkeypatch, "receiveAlert")
    sub._receive_message(None, None, alert())
    check(runner, expect={"count": 1})


@pytest.mark.parametrize(
    "message, error",
    [
        (alert(UserProperty=None), "headers \\(user properties\\)"),
        (alert(qos=0), "its qos differs"),
        (alert(MessageExpiryInterval=None), "message expiry interval"),
        (alert(MessageExpiryInterval=61), "message expiry interval"),
        (alert(ContentType="text/plain"), "its contentType property differs"),
        (alert(PayloadFormatIndicator=0), "its payloadFormatIndicator property differs"),
        (alert(ResponseTopic="elsewhere"), "its responseTopic property doesn't match"),
        (alert(CorrelationData=None), "it has no correlationData property"),
        (alert(CorrelationData=b"xyz"), "its correlationData property doesn't match"),
    ],
)
def test_message_breaking_headers_or_bindings_fails_the_consuming_step(runner, monkeypatch, broker, message, error):
    sub = subscribe(runner, monkeypatch, "receiveAlert")
    sub._receive_message(None, None, message)
    with pytest.raises(ExpectationFailure, match=error):
        check(runner)


def test_received_properties_are_not_checked_before_mqtt_5(runner, monkeypatch, service):
    service(protocol=3.11)
    sub = subscribe(runner, monkeypatch, "receiveAlert")
    sub._receive_message(
        None, None, SimpleNamespace(topic="alerts", payload=b'{"level": "low"}', properties=None, qos=1)
    )
    check(runner, expect={"count": 1})
