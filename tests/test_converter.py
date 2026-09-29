from types import SimpleNamespace

import httpx
import paho.mqtt.properties as props
import pytest
from paho.mqtt.packettypes import PacketTypes

from dugway.builtin_steps import ConvertFrom, ConvertTo, JsonPath
from dugway.capabilities import MultiValueCapability, RawContentCapability, ValueCapability
from dugway.converter import Converter
from dugway.expectations import ExpectationFailure, InvalidTestConfig
from dugway.json import JsonConverter
from dugway.mqtt import MqttMessage, MqttPublish, MqttSubscribe
from dugway.reporter import NoOpReporter
from dugway.runner import DugwayRunner
from dugway.schema import build_suite_schema, converter_types
from dugway.web import HttpRequest, HttpService
from helpers import SourceStep


class CsvConverter(Converter):
    """Stands in for a converter of another format: a list of strings, as comma separated text."""

    content_type = "text/csv"
    consumes = ("text/csv",)

    @classmethod
    def config_schema(cls):
        return {"type": "object", "properties": {"separator": {"type": "string"}}, "additionalProperties": False}

    def serialize(self, value, config=None):
        return (config or {}).get("separator", ",").join(value).encode()

    def deserialize(self, data, config=None):
        if not data:
            raise ExpectationFailure("Content was empty", "CSV", data)
        return data.decode().split((config or {}).get("separator", ","))


def add_converter(runner, name, converter_class=None):
    converter_class = converter_class or CsvConverter
    runner.get_suite()._converters[name] = converter_class(runner, {"type": name})


@pytest.fixture
def csv(runner):
    """Adds a CSV converter to the suite, named 'csv'."""
    add_converter(runner, "csv")


def test_json_converter_round_trips():
    converter = JsonConverter(None, {"type": "json"})
    assert converter.serialize({"a": [1, None]}) == b'{"a": [1, null]}'
    assert converter.deserialize(b'{"a": [1, null]}') == {"a": [1, None]}


def test_json_converter_fails_on_content_that_is_not_json():
    with pytest.raises(ExpectationFailure, match="was not JSON"):
        JsonConverter(None, {"type": "json"}).deserialize(b"\xff")


def test_json_is_an_installed_converter():
    assert converter_types()["json"] is JsonConverter


def suite_runner(tmp_path, converters):
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(f"services: {{}}\nconverters: {converters}\ntestCases: {{}}\n")
    return DugwayRunner(str(suite_file), NoOpReporter())


def test_suite_converters_are_found_by_name(tmp_path):
    runner = suite_runner(tmp_path, "{named: {type: json}}")
    assert isinstance(runner.get_converter("named"), JsonConverter)
    assert isinstance(runner.get_converter(None), JsonConverter)


def test_unknown_converter_name(tmp_path):
    runner = suite_runner(tmp_path, "{}")
    with pytest.raises(InvalidTestConfig, match="no converter named 'missing'"):
        runner.get_converter("missing")


def test_suite_schema_checks_converters():
    converters = build_suite_schema()["properties"]["converters"]["additionalProperties"]
    assert converters["allOf"][1] == {"properties": {"type": {"enum": sorted(converter_types())}}}


def test_mqtt_publish_serializes_with_the_converter(runner, csv):
    step = MqttPublish(
        runner, {"type": "mqtt_publish", "service": "s", "topic": "t", "converter": "csv", "json": ["a", "b"]}
    )
    assert step.outgoing_message().payload == b"a,b"


def subscription(runner, monkeypatch, *payloads):
    sub = MqttSubscribe(runner, {"type": "mqtt_subscribe", "service": "broker", "topic": "t"})
    for payload in payloads:
        sub._receive_message(None, None, SimpleNamespace(topic="t", payload=payload, properties=None))
    monkeypatch.setattr(runner, "get_step", lambda step_id: sub)
    return sub


def test_mqtt_message_deserializes_with_the_converter(runner, monkeypatch, csv):
    subscription(runner, monkeypatch, b"a,b")
    MqttMessage(
        runner,
        {"type": "mqtt_message", "from": "sub", "converter": "csv", "expect": {"json_schema": {"const": ["a", "b"]}}},
    ).run()


def test_mqtt_message_conversion_failure_names_the_topic(runner, monkeypatch, csv):
    subscription(runner, monkeypatch, b"")
    with pytest.raises(ExpectationFailure, match="Message on 't': Content was empty"):
        MqttMessage(runner, {"type": "mqtt_message", "from": "sub", "converter": "csv"}).run()


def test_json_step_converts_with_the_converter(runner, monkeypatch, csv):
    subscription(runner, monkeypatch, b"a,b", b"c")
    step = ConvertFrom(runner, {"type": "deserialize", "from": "sub", "converter": "csv"})
    step.run()
    first = step.multi_value_cap.get_content()
    assert (first.content, first.properties) == (["a", "b"], {"topic": "t", "contentType": None})
    assert step.multi_value_cap.get() == ["c"]


@pytest.fixture
def sent(runner, monkeypatch):
    """Adds an http service that answers every request with a CSV body, recording the requests."""
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append(kwargs)
        return httpx.Response(200, content=b"x,y", headers={"Content-Type": "text/csv"})

    monkeypatch.setattr(httpx, "request", fake_request)
    service = HttpService(runner, {"type": "http", "hostname": "example.com"})
    monkeypatch.setattr(runner, "get_service", lambda name: service)
    return requests


def test_http_request_converts_with_the_converter(runner, sent, csv):
    step = HttpRequest(
        runner,
        {"type": "http_request", "service": "api", "path": "/", "method": "POST", "converter": "csv", "payload": ["a"]},
    )
    step.run()
    assert sent[0]["content"] == b"a"
    assert sent[0]["headers"]["Content-Type"] == "text/csv"
    assert step._value_cap.get() == ["x", "y"]


def test_http_response_is_converted_by_its_content_type(runner, sent, csv):
    step = HttpRequest(runner, {"type": "http_request", "service": "api", "path": "/"})
    step.run()
    assert step._value_cap.get() == ["x", "y"]


def test_http_response_no_converter_consumes_gives_no_value(runner, sent):
    step = HttpRequest(runner, {"type": "http_request", "service": "api", "path": "/"})
    step.run()
    assert not step._value_cap.is_set


def test_http_response_that_does_not_convert_gives_no_value(runner, monkeypatch, sent):
    monkeypatch.setattr(httpx, "request", lambda method, url, **kwargs: httpx.Response(200, html="<p>hi</p>"))
    step = HttpRequest(runner, {"type": "http_request", "service": "api", "path": "/"})
    step.run()
    assert not step._value_cap.is_set


def test_http_response_the_given_converter_cannot_convert_fails(runner, monkeypatch, sent):
    monkeypatch.setattr(httpx, "request", lambda method, url, **kwargs: httpx.Response(200, html="<p>hi</p>"))
    step = HttpRequest(runner, {"type": "http_request", "service": "api", "path": "/", "converter": "json"})
    with pytest.raises(ExpectationFailure, match="was not JSON"):
        step.run()


def test_jsonpath_searches_an_http_response_value(runner, monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda method, url, **kwargs: httpx.Response(200, json={"id": 7}))
    service = HttpService(runner, {"type": "http", "hostname": "example.com"})
    monkeypatch.setattr(runner, "get_service", lambda name: service)
    request = HttpRequest(runner, {"type": "http_request", "service": "api", "path": "/"})
    request.run()
    monkeypatch.setattr(runner, "get_step", lambda step_id: request)
    found = JsonPath(runner, {"type": "jsonpath", "from": "request", "pointer": "/id"})
    found.run()
    assert found.value_cap.get() == 7


@pytest.mark.parametrize(
    "content_type, consumed",
    [
        ("application/json", True),
        ("Application/JSON; charset=utf-8", True),
        ("application/problem+json", True),
        ("text/plain", False),
    ],
)
def test_json_converter_consumes_json_types(content_type, consumed):
    assert JsonConverter(None, {"type": "json"}).consumes_type(content_type) is consumed


def test_content_without_a_type_is_converted_as_json(runner, csv):
    assert isinstance(runner.converter_for(None), JsonConverter)


def test_content_is_converted_by_the_converter_consuming_its_type(runner, csv):
    assert isinstance(runner.converter_for("text/csv; header=present"), CsvConverter)
    assert isinstance(runner.converter_for("application/json"), JsonConverter)


def test_content_of_a_type_no_converter_consumes(runner, csv):
    with pytest.raises(ExpectationFailure, match="No converter consumes content of type 'image/png'"):
        runner.converter_for("image/png")


def test_content_of_a_type_several_converters_consume(runner, csv):
    add_converter(runner, "other_csv")
    with pytest.raises(InvalidTestConfig, match="csv, other_csv all consume 'text/csv'"):
        runner.converter_for("text/csv")


def test_suite_converter_is_chosen_over_json(runner):
    add_converter(runner, "named_json", JsonConverter)
    assert runner.converter_for("application/json") is runner.get_converter("named_json")


def received_with_type(sub, payload, content_type):
    properties = props.Properties(PacketTypes.PUBLISH)
    properties.ContentType = content_type
    sub._receive_message(None, None, SimpleNamespace(topic="t", payload=payload, properties=properties))


def test_mqtt_message_converts_each_message_by_its_content_type(runner, monkeypatch, csv):
    sub = subscription(runner, monkeypatch)
    received_with_type(sub, b"a,b", "text/csv")
    received_with_type(sub, b'["a", "b"]', "application/json")
    MqttMessage(runner, {"type": "mqtt_message", "from": "sub", "expect": {"json_schema": {"const": ["a", "b"]}}}).run()


def test_given_converter_is_used_whatever_the_content_type(runner, monkeypatch, csv):
    raw = RawContentCapability(runner, {})
    raw.set_content(b"a,b", {"contentType": "text/plain"})
    monkeypatch.setattr(runner, "get_step", lambda step_id: SourceStep(runner, [raw]))
    step = ConvertFrom(runner, {"type": "deserialize", "from": "src", "converter": "csv"})
    step.run()
    assert step.value_cap.get() == ["a", "b"]


def test_json_step_converts_an_http_response_by_its_content_type(runner, monkeypatch, sent, csv):
    request = HttpRequest(runner, {"type": "http_request", "service": "api", "path": "/"})
    request.run()
    monkeypatch.setattr(runner, "get_step", lambda step_id: request)
    step = ConvertFrom(runner, {"type": "deserialize", "from": "request"})
    step.run()
    assert step.value_cap.get() == ["x", "y"]


def source_of(runner, monkeypatch, *capabilities):
    monkeypatch.setattr(runner, "get_step", lambda step_id: SourceStep(runner, list(capabilities)))


def test_serialize_needs_a_converter(runner):
    with pytest.raises(InvalidTestConfig):
        ConvertTo(runner, {"type": "serialize", "from": "src"})


def test_serialize_serializes_a_value(runner, monkeypatch, csv):
    value = ValueCapability(runner, {})
    value.set(["a", "b"])
    source_of(runner, monkeypatch, value)
    step = ConvertTo(runner, {"type": "serialize", "from": "src", "converter": "csv"})
    step.run()
    content = step.raw_content_cap.get_content()
    assert (content.content, content.properties) == (b"a,b", {"contentType": "text/csv"})


def test_serialize_serializes_each_value_keeping_its_properties(runner, monkeypatch, csv):
    values = MultiValueCapability(runner, {})
    values.add_content(["a"], {"topic": "t/a"})
    values.add_content(["b", "c"], {"topic": "t/b"})
    source_of(runner, monkeypatch, values)
    step = ConvertTo(runner, {"type": "serialize", "from": "src", "converter": "csv"})
    step.run()
    received = [step.raw_multi_cap.get_content(), step.raw_multi_cap.get_content()]
    assert [(c.content, c.properties) for c in received] == [
        (b"a", {"topic": "t/a", "contentType": "text/csv"}),
        (b"b,c", {"topic": "t/b", "contentType": "text/csv"}),
    ]


def test_serialize_serializes_a_value_found_by_jsonpath(runner, monkeypatch, csv):
    value = ValueCapability(runner, {})
    value.set({"letters": ["x", "y"]})
    source_of(runner, monkeypatch, value)
    found = JsonPath(runner, {"type": "jsonpath", "from": "src", "pointer": "/letters"})
    found.run()
    monkeypatch.setattr(runner, "get_step", lambda step_id: found)
    step = ConvertTo(runner, {"type": "serialize", "from": "found", "converter": "csv"})
    step.run()
    assert step.raw_content_cap.content == b"x,y"


def test_converted_content_converts_back_by_its_type(runner, monkeypatch, csv):
    value = ValueCapability(runner, {})
    value.set(["a", "b"])
    source_of(runner, monkeypatch, value)
    to = ConvertTo(runner, {"type": "serialize", "from": "src", "converter": "csv"})
    to.run()
    monkeypatch.setattr(runner, "get_step", lambda step_id: to)
    back = ConvertFrom(runner, {"type": "deserialize", "from": "to"})
    back.run()
    assert back.value_cap.get() == ["a", "b"]


def test_converters_take_no_config_by_default():
    JsonConverter.check_config(None)
    JsonConverter.check_config({})
    with pytest.raises(InvalidTestConfig, match="Invalid converterConfig"):
        JsonConverter.check_config({"indent": 2})


def test_converter_config_is_checked_against_its_schema():
    CsvConverter.check_config({"separator": ";"})
    with pytest.raises(InvalidTestConfig):
        CsvConverter.check_config({"separator": 1})


def test_converter_config_is_given_to_the_converter(runner, monkeypatch, csv):
    publish = MqttPublish(
        runner,
        {
            "type": "mqtt_publish",
            "service": "s",
            "topic": "t",
            "converter": "csv",
            "converterConfig": {"separator": ";"},
            "json": ["a", "b"],
        },
    )
    message = publish.outgoing_message()
    assert message.payload == b"a;b"
    subscription(runner, monkeypatch, message.payload)
    MqttMessage(
        runner,
        {
            "type": "mqtt_message",
            "from": "sub",
            "converter": "csv",
            "converterConfig": {"separator": ";"},
            "expect": {"json_schema": {"const": ["a", "b"]}},
        },
    ).run()


def test_converter_config_the_converter_does_not_accept(runner, monkeypatch):
    subscription(runner, monkeypatch, b"[1]")
    step = MqttMessage(runner, {"type": "mqtt_message", "from": "sub", "converterConfig": {"separator": ";"}})
    with pytest.raises(InvalidTestConfig, match="Invalid converterConfig"):
        step.run()


def test_deserialized_messages_are_checked_by_mqtt_message(runner, monkeypatch):
    subscription(runner, monkeypatch, b'{"a": 1}', b'{"a": 2}')
    step = ConvertFrom(runner, {"type": "deserialize", "from": "sub"})
    step.run()
    monkeypatch.setattr(runner, "get_step", lambda step_id: step)
    MqttMessage(
        runner,
        {
            "type": "mqtt_message",
            "from": "values",
            "expect": {"count": 2, "topic": "t", "json_schema": {"required": ["a"]}},
        },
    ).run()


def test_deserialized_value_is_checked_by_mqtt_message(runner, monkeypatch):
    raw = RawContentCapability(runner, {})
    raw.set_content(b'{"a": 1}', {})
    source_of(runner, monkeypatch, raw)
    step = ConvertFrom(runner, {"type": "deserialize", "from": "src"})
    step.run()
    monkeypatch.setattr(runner, "get_step", lambda step_id: step)
    with pytest.raises(ExpectationFailure, match="did not match the JSON Schema"):
        MqttMessage(
            runner, {"type": "mqtt_message", "from": "value", "expect": {"json_schema": {"required": ["b"]}}}
        ).run()


def test_deserialized_messages_are_searched_by_jsonpath(runner, monkeypatch):
    subscription(runner, monkeypatch, b'{"a": 1}', b'{"a": 2}')
    step = ConvertFrom(runner, {"type": "deserialize", "from": "sub"})
    step.run()
    monkeypatch.setattr(runner, "get_step", lambda step_id: step)
    found = JsonPath(runner, {"type": "jsonpath", "from": "values", "path": "$.a", "minimum": 2})
    found.run()
    assert found.value_cap.get() == 1


def test_deserialized_messages_serialize_keeping_their_topics(runner, monkeypatch, csv):
    subscription(runner, monkeypatch, b'["a"]')
    step = ConvertFrom(runner, {"type": "deserialize", "from": "sub"})
    step.run()
    monkeypatch.setattr(runner, "get_step", lambda step_id: step)
    to = ConvertTo(runner, {"type": "serialize", "from": "values", "converter": "csv"})
    to.run()
    content = to.raw_multi_cap.get_content()
    assert (content.content, content.properties["topic"]) == (b"a", "t")
