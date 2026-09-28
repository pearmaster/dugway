import httpx
import pytest

from dugway.web import HttpRequest, HttpService


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
def test_falsy_json_body_is_sent(runner, sent, body):
    request_step(runner, method="POST", json=body).run()
    assert sent[0]["json"] == body
    assert sent[0]["headers"]["Content-Type"] == "application/json"


def test_request_does_not_modify_its_config(runner, sent):
    step = request_step(runner, method="POST", headers={}, json={"a": 1})
    step.run()
    assert step._config["headers"] == {}
