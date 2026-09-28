"""A failing test case must be reported as a failure by the CLI and the pytest plugin."""

import subprocess
import sys
from xml.etree import ElementTree

PASSING_SUITE = """
services: {}
testCases:
  passes:
    steps:
      - type: sleep
        time: 0
"""

FAILING_SUITE = """
services: {}
testCases:
  passes:
    steps:
      - type: sleep
        time: 0
  fails:
    steps:
      - id: save_missing
        type: save
        from: does_not_exist
        case: x
"""


def run_cli(tmp_path, suite_text, *args, command="run"):
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(suite_text)
    return subprocess.run(
        [sys.executable, "-m", "dugway.cli", command, *args, str(suite_file)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_exits_zero_when_suite_passes(tmp_path):
    assert run_cli(tmp_path, PASSING_SUITE).returncode == 0


def test_cli_exits_nonzero_when_a_case_fails(tmp_path):
    assert run_cli(tmp_path, FAILING_SUITE).returncode == 1


def test_cli_writes_junit_report_when_requested(tmp_path):
    report = tmp_path / "results.xml"
    assert run_cli(tmp_path, FAILING_SUITE, "--junit", str(report)).returncode == 1
    suite = ElementTree.parse(report).getroot().find("testsuite")
    assert suite.get("tests") == "2"
    assert suite.get("failures") == "1"


def test_cli_writes_no_junit_report_by_default(tmp_path):
    assert run_cli(tmp_path, PASSING_SUITE).returncode == 0
    assert list(tmp_path.glob("*.xml")) == []


def test_pytest_plugin_reports_failing_case(pytester):
    pytester.makefile(".dugway.yaml", suite=FAILING_SUITE)
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*Step 'save_missing'*"])
