import json
import subprocess
import sys
from xml.etree import ElementTree

from jacobsjsonschema.draft7 import Validator

from test_reporting import FAILING_SUITE, PASSING_SUITE, run_cli

INVALID_SUITES = {
    "step missing the service it uses": """
services: {}
testCases:
  a:
    steps:
      - type: mqtt_subscribe
        topic: t
""",
    "step missing the step it reads from": """
services: {}
testCases:
  a:
    steps:
      - type: jsonpath
        path: $.a
""",
    "invalid capability option": """
services: {}
testCases:
  a:
    steps:
      - type: json
        from: response
        expect:
          json_schema: not a schema
""",
    "unknown step type": """
services: {}
testCases:
  a:
    steps:
      - type: no_such_step
""",
    "step missing required field": """
services: {}
testCases:
  a:
    steps:
      - type: mqtt_publish
        service: broker
""",
    "invalid service field": """
services:
  broker:
    type: mqtt
    protocol: 4
testCases: {}
""",
    "missing testCases": """
services: {}
""",
}


def test_validate_accepts_valid_suite(tmp_path):
    # A suite whose test case fails is still a valid file.
    result = run_cli(tmp_path, FAILING_SUITE, command="validate")
    assert result.returncode == 0
    assert "is valid" in result.stdout


def test_validate_rejects_invalid_suites(tmp_path):
    for description, suite in INVALID_SUITES.items():
        result = run_cli(tmp_path, suite, command="validate")
        assert result.returncode == 1, description
        assert "is invalid" in result.stderr, description


def test_validate_accepts_example_suite():
    result = subprocess.run(
        [sys.executable, "-m", "dugway.cli", "validate", "examples/examples.yaml"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_schema_prints_valid_json_schema():
    result = subprocess.run(
        [sys.executable, "-m", "dugway.cli", "schema"],
        capture_output=True,
        text=True,
        check=True,
    )
    schema = json.loads(result.stdout)
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    # Every registered step type is covered.
    step_types = schema["properties"]["testCases"]["additionalProperties"]["allOf"][1]
    step_type_enum = step_types["properties"]["steps"]["items"]["allOf"][1]
    assert {"sleep", "mqtt_publish", "http_request"} <= set(step_type_enum["properties"]["type"]["enum"])
    Validator(schema).validate({"services": {}, "testCases": {}})


def run_cli_files(tmp_path, command, suites, *args):
    """Runs the CLI on one suite file per entry of `suites`, named after its key."""
    paths = []
    for name, text in suites.items():
        path = tmp_path / f"{name}.yaml"
        path.write_text(text)
        paths.append(str(path))
    return subprocess.run(
        [sys.executable, "-m", "dugway.cli", command, *args, *paths],
        capture_output=True,
        text=True,
        check=False,
    )


def junit_suites(report):
    return {suite.get("name"): suite for suite in ElementTree.parse(report).getroot().iter("testsuite")}


def test_run_passes_when_every_suite_passes(tmp_path):
    result = run_cli_files(tmp_path, "run", {"one": PASSING_SUITE, "two": PASSING_SUITE})
    assert result.returncode == 0


def test_run_runs_every_suite_and_fails_if_any_fails(tmp_path):
    report = tmp_path / "results.xml"
    suites = {"fails": FAILING_SUITE, "passes": PASSING_SUITE}
    result = run_cli_files(tmp_path, "run", suites, "--junit", str(report))
    assert result.returncode == 1
    reported = junit_suites(report)
    assert set(reported) == {"fails.yaml", "passes.yaml"}
    assert reported["fails.yaml"].get("failures") == "1"
    assert reported["passes.yaml"].get("failures") == "0"


def test_run_skips_invalid_suite_and_runs_the_rest(tmp_path):
    report = tmp_path / "results.xml"
    suites = {"invalid": INVALID_SUITES["missing testCases"], "passes": PASSING_SUITE}
    result = run_cli_files(tmp_path, "run", suites, "--junit", str(report))
    assert result.returncode == 1
    assert "invalid.yaml is invalid" in result.stderr
    assert set(junit_suites(report)) == {"passes.yaml"}


def test_validate_checks_every_file(tmp_path):
    suites = {"bad": INVALID_SUITES["unknown step type"], "good": PASSING_SUITE}
    result = run_cli_files(tmp_path, "validate", suites)
    assert result.returncode == 1
    assert "bad.yaml is invalid" in result.stderr
    assert "good.yaml is valid" in result.stdout
