import json

import httpx
import pytest

from dugway.capabilities import RawContentCapability, ValueCapability
from dugway.expectations import FailedTestStep, InvalidTestConfig
from dugway.web import HttpRequest, HttpService
from helpers import SourceStep


@pytest.fixture
def sent(runner, monkeypatch):
    """Adds an http service named 'api' and records the requests sent through httpx."""
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(httpx, "request", fake_request)
    service = HttpService(runner, {"type": "http", "hostname": "example.com"})
    monkeypatch.setattr(runner, "get_service", lambda name: service)
    return requests


def request_step(runner, **config):
    return HttpRequest(runner, {"type": "http_request", "service": "api", "path": "/", **config})


def test_request_headers(runner, sent):
    request_step(runner, headers={"X-Test": "yes"}).run()
    assert sent[0]["headers"]["X-Test"] == "yes"


@pytest.mark.parametrize("body", [{}, [], 0, False])
def test_falsy_payload_is_sent(runner, sent, body):
    request_step(runner, method="POST", payload=body).run()
    assert json.loads(sent[0]["content"]) == body
    assert sent[0]["headers"]["Content-Type"] == "application/json"


def test_request_does_not_modify_its_config(runner, sent):
    step = request_step(runner, method="POST", headers={}, payload={"a": 1})
    step.run()
    assert step._config["headers"] == {}


def test_payload_and_from_cannot_both_be_given(runner):
    with pytest.raises(InvalidTestConfig):
        request_step(runner, method="POST", payload={}, **{"from": "earlier"})


def earlier_step(runner, monkeypatch, *capabilities):
    monkeypatch.setattr(runner, "get_step", lambda step_id: SourceStep(runner, list(capabilities)))


def test_content_from_an_earlier_step_is_sent_as_it_is(runner, sent, monkeypatch):
    raw = RawContentCapability(runner, {})
    raw.set_content(b"a,b", {"contentType": "text/csv"})
    earlier_step(runner, monkeypatch, raw)
    request_step(runner, method="POST", **{"from": "earlier"}).run()
    assert sent[0]["content"] == b"a,b"
    assert sent[0]["headers"]["Content-Type"] == "text/csv"


def test_value_from_an_earlier_step_is_serialized(runner, sent, monkeypatch):
    value = ValueCapability(runner, {})
    value.set({"id": 7})
    earlier_step(runner, monkeypatch, value)
    request_step(runner, method="POST", **{"from": "earlier"}).run()
    assert json.loads(sent[0]["content"]) == {"id": 7}
    assert sent[0]["headers"]["Content-Type"] == "application/json"


def test_from_a_step_without_content_or_a_value(runner, sent, monkeypatch):
    earlier_step(runner, monkeypatch, ValueCapability(runner, {}))
    with pytest.raises(FailedTestStep, match="did not provide content or a value"):
        request_step(runner, method="POST", **{"from": "earlier"}).run()
