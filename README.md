# dugway

**Dugway API testing framework**

Dugway is heavily inspired by Tavern.  Tavern is mature and Dugway is still under initial development.  The main difference is that Tavern is a little more structured and simple, where Dugway is a little more flexible and powerful (but perhaps at the expense of some simplicity).

When Dugway is feature complete, it will have these features:

* More flexibility.  It will have the ability to test more than just HTTP and MQTT, depending on plugins provided.  Out of the box it will come with a SSE-stream plugin.
* MQTTv5 support.  It will have the ability to ensure that received MQTT messages have specific MQTTv5 properties set to specific values.
* JSON Schema validation.  Assert as much or as little of the response JSON that you want, by providing a JSON Schema for validation.
* API Spec integration.  Call operations from an OpenAPI or AsyncAPI document, and have requests, responses and messages checked against it automatically.
* Pytest integration.  Run Dugway and get reports through pytest invocation.

## Installation

This Python project is managed by [uv](https://github.com/astral-sh/uv) from Astral, and requires Python 3.12+.

To hack on Dugway itself, clone the repo and sync the environment:

```bash
uv sync
uv run dugway --help
```

To use Dugway in another project, build and install it as a regular package:

```bash
uv build
pip install dist/dugway-*.whl
```

This installs the `dugway` CLI as well as a pytest plugin that auto-discovers `*.dugway.yaml` / `*.dugway.yml` files.

## Usage

A test suite is a YAML file that declares one or more `services` to talk to and one or more `testCases` made of `steps`:

```yaml
services:
  catFacts:
    type: http
    hostname: "{{ env.CAT_FACT_HOSTNAME | default('catfact.ninja', true) }}"
    tls: true

testCases:
  getACatFact:
    steps:
      - id: get_a_cat_fact
        type: http_request
        service: catFacts
        path: /fact
        expect:
          status_code: 200
          json_schema:
            type: object
            properties:
              fact:
                type: string
            required:
              - fact
```

Values throughout a suite file are rendered as Jinja2 templates, with `env.NAME` for environment variables and `suite.name` / `case.name` for variables saved by earlier steps. Services are set up before each test case and torn down after it.

More examples, including MQTT pub/sub, can be found in [`examples/examples.yaml`](examples/examples.yaml) and [`self_tests/`](self_tests/).

### OpenAPI

An `openapi` service points at an OpenAPI 3 document, and `openapi_request` steps call its operations by `operationId`. Parameters are given by name and sent wherever the document places them (path, query string, header or cookie), and a `json` or `content` request body is sent with the content type the document lists. Every response is checked against the document before the step's own `expect` checks run: its status code and content type must be documented, required response headers must be present, and a JSON body must match the documented schema.

```yaml
services:
  petstore:
    type: openapi
    spec: petstore.openapi.yaml      # relative to this suite file
    baseUrl: http://localhost:8080   # optional; defaults to the document's first server

testCases:
  addAndFetchAPet:
    steps:
      - id: add_pet
        type: openapi_request
        service: petstore
        operationId: addPet
        json:
          name: Rex
        expect:
          status_code: 201
      - id: pet_id
        type: jsonpath
        from: add_pet
        pointer: /id
      - type: save
        from: pet_id
        case: petId
      - type: openapi_request
        service: petstore
        operationId: getPet
        parameters:
          petId: "{{ case.petId }}"
        expect:
          status_code: 200
```

A step fails before sending anything when it gives a parameter the operation doesn't have, leaves out a required parameter, or leaves out a required request body. A request body is checked too: its content type must be documented, and a JSON body must match the documented schema. To deliberately send a request that breaks the contract, use an `http_request` step instead. `dugway help services openapi` and `dugway help steps openapi_request` list every option.

### AsyncAPI

An `asyncapi` service points at an AsyncAPI 2 or 3 document and connects to its MQTT server. The hostname, port, TLS and protocol version come from the document's first MQTT server, or the one named by `server`. The client id, clean session, keep alive, session expiry, maximum packet size and last will come from that server's MQTT binding. Any `mqtt` service option given alongside takes precedence. `asyncapi_publish` and `asyncapi_subscribe` steps work like `mqtt_publish` and `mqtt_subscribe`, but take an `operationId` in place of a topic. The topic is the operation's channel address, with the `parameters` given filled in.

```yaml
services:
  hello:
    type: asyncapi
    spec: hello.asyncapi.yaml        # relative to this suite file

testCases:
  pingPong:
    steps:
      - id: pongs
        type: asyncapi_subscribe
        service: hello
        operationId: receivePong
      - type: asyncapi_publish
        service: hello
        operationId: sendPing
        json:
          greeting: hi
          count: 1
      - type: mqtt_message
        from: pongs
        timeoutSeconds: 5
        expect:
          count: 1
```

Each step must use an operation in the direction the document gives it. `send` operations in AsyncAPI 3 and `publish` operations in AsyncAPI 2 are used by `asyncapi_publish`. `receive` and `subscribe` operations are used by `asyncapi_subscribe`.

A published message must match one of the operation's messages, or the step fails without publishing. Its payload is checked against the message's payload schema, and its `publishProperties.userProperties` against the message's headers schema. Every message a subscription receives is checked the same way, or the `mqtt_message` step reading it fails before its own `expect` checks run.

The document's MQTT bindings are checked too. An operation binding's `qos`, `retain` and `messageExpiryInterval`, and a message binding's `contentType`, `payloadFormatIndicator` and fixed `responseTopic`, are used when publishing unless the step gives them. A step that gives a different value fails. A `correlationData` or `responseTopic` schema is checked against the value the step gives. Received messages must have the binding's qos and properties, and a message expiry interval no longer than the binding's. Headers and properties exist only in MQTTv5, so on earlier protocol versions they aren't checked.

A parameter left out of a subscription matches any value, using MQTT's `+` wildcard. A parameter left out of a publish takes its documented default. Payload schemas may be JSON Schema, which is AsyncAPI's default, or OpenAPI 3.0 schemas. The full example is in [`self_tests/`](self_tests/), and `dugway help services asyncapi` lists every option.

### CLI

```bash
dugway run tests/*.yaml            # run one or more suites and report results
dugway run --junit results.xml tests/*.yaml   # also write a JUnit XML report
dugway validate tests/*.yaml       # check suite files against the schema without running them
dugway schema                      # print the JSON Schema suite files must comply with
dugway help                        # browse available service and step types and their options
```

Exit status is 0 when every test case passes, and 1 if any test fails or any suite file is invalid.

### Pytest integration

Dugway ships a pytest plugin, so any `*.dugway.yaml` file discovered by pytest is collected and run as ordinary test cases, one per entry under `testCases`:

```bash
pytest tests/
```

Each test case is reported as its own pytest item, with failing steps shown in the failure output.

## License

Apache 2.0 License.