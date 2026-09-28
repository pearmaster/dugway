import json
from pathlib import Path

import httpx
import pytest

from dugway.capabilities import JsonContentCapability, TextContentCapability
from dugway.expectations import ExpectationFailure, FailedTestStep, InvalidTestConfig
from dugway.openapi import OpenApiRequest, OpenApiService
from dugway.reporter import NoOpReporter
from dugway.runner import DugwayRunner

SPEC = str(Path(__file__).parent / "petstore.openapi.yaml")

PET = {"id": 1, "name": "Rex", "tag": None}


class Exchange:
    """Records the request sent through httpx, and answers it with a configurable response."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.body = json.dumps(PET)
        self.headers = {"Content-Type": "application/json"}

    def respond(self, status=200, body=None, content_type="application/json", **headers):
        self.status = status
        self.body = json.dumps(body) if not isinstance(body, str) and body is not None else (body or "")
        self.headers = {**headers}
        if content_type:
            self.headers["Content-Type"] = content_type

    def __call__(self, method, url, **kwargs):
        request = httpx.Request(method, url, params=kwargs.get("params"))
        self.requests.append({"method": method, "url": str(request.url), **kwargs})
        # Given as bytes, since httpx adds a text/plain Content-Type to a response built from text
        return httpx.Response(self.status, content=self.body.encode(), headers=self.headers, request=request)

    @property
    def last(self):
        return self.requests[-1]


@pytest.fixture
def exchange(monkeypatch):
    exchange = Exchange()
    monkeypatch.setattr(httpx, "request", exchange)
    return exchange


@pytest.fixture
def service(runner, monkeypatch):
    def make(**config):
        service = OpenApiService(runner, {"type": "openapi", "spec": SPEC, **config})
        monkeypatch.setattr(runner, "get_service", lambda name: service)
        return service

    return make


@pytest.fixture
def api(service):
    return service(baseUrl="http://api.test/")


def step(runner, operation_id, **config):
    return OpenApiRequest(runner, {"type": "openapi_request", "service": "pets", "operationId": operation_id, **config})


def test_parameters_are_placed_where_the_document_says(runner, api, exchange):
    step(
        runner,
        "getPet",
        parameters={"petId": 7, "verbose": True, "X-Trace": "abc", "session": "s1"},
    ).run()
    assert exchange.last["method"] == "GET"
    assert exchange.last["url"] == "http://api.test/pets/7?verbose=true"
    assert exchange.last["headers"]["X-Trace"] == "abc"
    assert exchange.last["cookies"] == {"session": "s1"}


def test_path_parameters_are_url_encoded(runner, api, exchange):
    exchange.respond(404, content_type=None)
    step(runner, "getPet", parameters={"petId": "a/b c"}).run()
    assert exchange.last["url"] == "http://api.test/pets/a%2Fb%20c"


def test_array_query_parameters_repeat_the_name(runner, api, exchange):
    exchange.respond(200, [PET], **{"X-Total-Count": "1"})
    step(runner, "listPets", parameters={"tags": ["a", "b"], "limit": 5}).run()
    assert exchange.last["url"] == "http://api.test/pets?tags=a&tags=b&limit=5"


def test_parameter_templates_are_evaluated(runner, api, exchange):
    runner.get_suite().add_variable("pet", 3)
    step(runner, "getPet", parameters={"petId": "{{ suite.pet }}"}).run()
    assert exchange.last["url"] == "http://api.test/pets/3"


def test_undocumented_parameter_fails_before_sending(runner, api, exchange):
    with pytest.raises(FailedTestStep, match="no parameter named 'colour'"):
        step(runner, "getPet", parameters={"petId": 1, "colour": "red"}).run()
    assert exchange.requests == []


def test_missing_required_parameter_fails_before_sending(runner, api, exchange):
    with pytest.raises(FailedTestStep, match="requires the parameter"):
        step(runner, "getPet").run()
    assert exchange.requests == []


def test_unknown_operation_fails(runner, api, exchange):
    with pytest.raises(FailedTestStep, match="no operation with operationId 'flyPet'"):
        step(runner, "flyPet").run()


def test_json_body_uses_the_documented_content_type(runner, api, exchange):
    exchange.respond(201, PET)
    step(runner, "addPet", json={"name": "Rex", "tag": None}).run()
    assert exchange.last["method"] == "POST"
    assert json.loads(exchange.last["content"]) == {"name": "Rex", "tag": None}
    assert exchange.last["headers"]["Content-Type"] == "application/json"


def test_text_body_and_content_type_override(runner, api, exchange):
    exchange.respond(201, PET)
    step(runner, "addPet", content="Rex", contentType="text/plain; charset=utf-8").run()
    assert exchange.last["content"] == "Rex"
    assert exchange.last["headers"]["Content-Type"] == "text/plain; charset=utf-8"


def test_missing_required_body_fails_before_sending(runner, api, exchange):
    with pytest.raises(FailedTestStep, match="requires a request body"):
        step(runner, "addPet").run()
    assert exchange.requests == []


def test_service_and_step_headers_are_sent(runner, service, exchange):
    service(baseUrl="http://api.test", headers={"Authorization": "Bearer t"})
    step(runner, "getPet", parameters={"petId": 1}, headers={"Accept": "application/json"}).run()
    assert exchange.last["headers"]["Authorization"] == "Bearer t"
    assert exchange.last["headers"]["Accept"] == "application/json"


def test_undocumented_status_code_fails(runner, api, exchange):
    exchange.respond(500, {"title": "boom"}, content_type="application/problem+json")
    with pytest.raises(ExpectationFailure, match="status 500 is not documented"):
        step(runner, "getPet", parameters={"petId": 1}).run()


def test_status_ranges_and_default_responses_are_documented(runner, api, exchange):
    exchange.respond(418, content_type=None)
    step(runner, "getPet", parameters={"petId": 1}).run()
    exchange.respond(503, {"title": "down"}, content_type="application/problem+json")
    step(runner, "addPet", json={"name": "Rex"}).run()


def test_undocumented_content_type_fails(runner, api, exchange):
    exchange.respond(200, "<pet/>", content_type="application/xml")
    with pytest.raises(ExpectationFailure, match="content type 'application/xml' is not documented"):
        step(runner, "getPet", parameters={"petId": 1}).run()


def test_missing_content_type_fails_when_content_is_documented(runner, api, exchange):
    exchange.respond(200, "", content_type=None)
    with pytest.raises(ExpectationFailure, match=r"content type '\(none\)' is not documented"):
        step(runner, "getPet", parameters={"petId": 1}).run()


def test_body_not_matching_the_schema_fails(runner, api, exchange):
    exchange.respond(200, {"id": "one", "name": "Rex"})
    with pytest.raises(ExpectationFailure, match="does not match the OpenAPI schema for the 200 response"):
        step(runner, "getPet", parameters={"petId": 1}).run()


def test_body_that_is_not_json_fails(runner, api, exchange):
    exchange.respond(200, "not json")
    with pytest.raises(ExpectationFailure, match="not valid JSON"):
        step(runner, "getPet", parameters={"petId": 1}).run()


def test_nullable_and_recursive_schemas_are_understood(runner, api, exchange):
    exchange.respond(200, {"id": 2, "name": "Pup", "tag": None, "parent": PET})
    step(runner, "getPet", parameters={"petId": 2}).run()


def test_missing_required_response_header_fails(runner, api, exchange):
    exchange.respond(200, [PET])
    with pytest.raises(ExpectationFailure, match="missing the header 'X-Total-Count'"):
        step(runner, "listPets").run()


def test_response_without_documented_content_is_not_checked(runner, api, exchange):
    exchange.respond(204, content_type=None)
    step(runner, "deletePet", parameters={"petId": 1}).run()


def test_document_checks_come_before_expectations(runner, api, exchange):
    # The status code the step expects is what came back, but the body breaks the document's schema
    exchange.respond(200, {"name": "Rex"})
    with pytest.raises(ExpectationFailure, match="does not match the OpenAPI schema"):
        step(runner, "getPet", parameters={"petId": 1}, expect={"status_code": 200}).run()


def test_expected_status_code_is_checked_after_the_document(runner, api, exchange):
    exchange.respond(404, content_type=None)
    with pytest.raises(ExpectationFailure, match="Status code") as failure:
        step(runner, "getPet", parameters={"petId": 1}, expect={"status_code": 200}).run()
    assert (failure.value.expected, failure.value.actual) == (200, 404)


def test_expected_json_schema_is_checked(runner, api, exchange):
    step(runner, "getPet", parameters={"petId": 1}, expect={"json_schema": {"properties": {"id": {"const": 1}}}}).run()
    with pytest.raises(ExpectationFailure, match="did not match the JSON Schema"):
        step(
            runner, "getPet", parameters={"petId": 1}, expect={"json_schema": {"properties": {"id": {"const": 2}}}}
        ).run()


def test_response_body_is_available_to_later_steps(runner, api, exchange):
    called = step(runner, "getPet", parameters={"petId": 1})
    called.run()
    assert called.get_capability(TextContentCapability.NAME).response_body == json.dumps(PET)
    assert called.get_capability(JsonContentCapability.NAME).json_content == PET


def test_default_base_url_comes_from_the_documents_servers(runner, service, exchange):
    service()
    step(runner, "getPet", parameters={"petId": 1}).run()
    assert exchange.last["url"] == "https://pets.example.com/v1/pets/1"


def write_spec(directory, name="api.yaml", version="3.1.0", **extra):
    spec = {
        "openapi": version,
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/things": {
                "get": {
                    "operationId": "listThings",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array", "items": {"type": ["string", "null"]}},
                                },
                            },
                        },
                    },
                },
            },
        },
        **extra,
    }
    path = directory / name
    path.write_text(json.dumps(spec))
    return path


def test_relative_server_url_needs_a_base_url(runner, tmp_path, monkeypatch, exchange):
    spec = write_spec(tmp_path, servers=[{"url": "/api"}])
    service = OpenApiService(runner, {"type": "openapi", "spec": str(spec)})
    monkeypatch.setattr(runner, "get_service", lambda name: service)
    with pytest.raises(FailedTestStep, match="relative, so the service needs a baseUrl"):
        step(runner, "listThings").run()


def test_openapi_31_schemas_are_json_schema_2020_12(runner, tmp_path, monkeypatch, exchange):
    spec = write_spec(tmp_path)
    service = OpenApiService(runner, {"type": "openapi", "spec": str(spec), "baseUrl": "http://t"})
    monkeypatch.setattr(runner, "get_service", lambda name: service)
    exchange.respond(200, ["a", None])
    step(runner, "listThings").run()
    exchange.respond(200, ["a", 1])
    with pytest.raises(ExpectationFailure, match="does not match the OpenAPI schema"):
        step(runner, "listThings").run()


def test_spec_path_is_relative_to_the_suite_file(tmp_path, exchange):
    (tmp_path / "specs").mkdir()
    write_spec(tmp_path / "specs", servers=[{"url": "http://t"}])
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "services:\n  things:\n    type: openapi\n    spec: specs/api.yaml\n"
        "testCases:\n  list:\n    steps:\n      - type: openapi_request\n        service: things\n"
        "        operationId: listThings\n        expect:\n          status_code: 200\n"
    )
    exchange.respond(200, ["a"])
    assert DugwayRunner(str(suite), NoOpReporter()).run() is True
    assert exchange.last["url"] == "http://t/things"


def test_missing_spec_file_makes_the_suite_invalid(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text("services:\n  things:\n    type: openapi\n    spec: nope.yaml\ntestCases: {}\n")
    with pytest.raises(InvalidTestConfig, match="Could not load the OpenAPI document"):
        DugwayRunner(str(suite), NoOpReporter())


def test_non_openapi_3_document_is_rejected(runner, tmp_path):
    spec = write_spec(tmp_path, version="2.0")
    with pytest.raises(InvalidTestConfig, match="not an OpenAPI 3 document"):
        OpenApiService(runner, {"type": "openapi", "spec": str(spec)})


def test_json_body_not_matching_the_schema_fails_before_sending(runner, api, exchange):
    with pytest.raises(FailedTestStep, match="Request body does not match the OpenAPI schema"):
        step(runner, "addPet", json={"tag": "no name"}).run()
    assert exchange.requests == []


def test_json_body_schema_understands_nullable(runner, api, exchange):
    exchange.respond(201, PET)
    step(runner, "addPet", json={"name": "Rex", "tag": None}).run()


def test_json_body_is_validated_after_templates_are_evaluated(runner, api, exchange):
    runner.get_suite().add_variable("name", "Rex")
    exchange.respond(201, PET)
    step(runner, "addPet", json={"name": "{{ suite.name }}"}).run()
    assert json.loads(exchange.last["content"]) == {"name": "Rex"}


def test_content_with_a_json_type_is_parsed_and_validated(runner, api, exchange):
    with pytest.raises(FailedTestStep, match="does not match the OpenAPI schema"):
        step(runner, "addPet", content='{"tag": "x"}', contentType="application/json").run()
    with pytest.raises(FailedTestStep, match="is not valid JSON"):
        step(runner, "addPet", content="{nope", contentType="application/json").run()
    assert exchange.requests == []


def test_undocumented_request_content_type_fails_before_sending(runner, api, exchange):
    with pytest.raises(FailedTestStep, match="Request content type 'application/xml' is not documented"):
        step(runner, "addPet", content="<pet/>", contentType="application/xml").run()
    assert exchange.requests == []


def test_body_for_an_operation_without_one_fails_before_sending(runner, api, exchange):
    with pytest.raises(FailedTestStep, match="does not take a request body"):
        step(runner, "deletePet", parameters={"petId": 1}, json={"why": "old"}).run()
    assert exchange.requests == []


def test_openapi_31_request_bodies_are_json_schema_2020_12(runner, tmp_path, monkeypatch, exchange):
    spec = write_spec(tmp_path)
    spec_doc = json.loads(spec.read_text())
    spec_doc["paths"]["/things"]["post"] = {
        "operationId": "addThing",
        "requestBody": {
            "content": {
                "application/json": {"schema": {"type": "object", "properties": {"n": {"type": ["integer", "null"]}}}}
            }
        },
        "responses": {"204": {"description": "ok"}},
    }
    spec.write_text(json.dumps(spec_doc))
    service = OpenApiService(runner, {"type": "openapi", "spec": str(spec), "baseUrl": "http://t"})
    monkeypatch.setattr(runner, "get_service", lambda name: service)
    exchange.respond(204, content_type=None)
    step(runner, "addThing", json={"n": None}).run()
    with pytest.raises(FailedTestStep, match="Request body does not match"):
        step(runner, "addThing", json={"n": "one"}).run()
