# dugway

**Dugway API testing framework**

Dugway is heavily inspired by Tavern.  Tavern is mature and Dugway is still under initial development.  The main difference is that Tavern is a little more structured and simple, where Dugway is a little more flexible and powerful (but perhaps at the expense of some simplicity).

When Dugway is feature complete, it will have these features:

* More flexibility.  It will have the ability to test more than just HTTP and MQTT, depending on plugins provided.  Out of the box it will come with a SSE-stream plugin.
* MQTTv5 support.  It will have the ability to ensure that received MQTT messages have specific MQTTv5 properties set to specific values.
* JSON Schema validation.  Assert as much or as little of the response JSON that you want, by providing a JSON Schema for validation.
* API Spec integration.  Hook into OpenAPI or AsyncAPI specs for automated assertions.
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