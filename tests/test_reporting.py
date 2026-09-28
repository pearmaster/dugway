"""A failing test case must be reported as a failure by the CLI and the pytest plugin."""

import subprocess
import sys

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


def run_cli(tmp_path, suite_text):
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(suite_text)
    return subprocess.run(
        [sys.executable, "-m", "dugway.cli", str(suite_file)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_exits_zero_when_suite_passes(tmp_path):
    assert run_cli(tmp_path, PASSING_SUITE).returncode == 0


def test_cli_exits_nonzero_when_a_case_fails(tmp_path):
    assert run_cli(tmp_path, FAILING_SUITE).returncode == 1


def test_pytest_plugin_reports_failing_case(pytester):
    pytester.makefile(".dugway.yaml", suite=FAILING_SUITE)
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*Step 'save_missing'*"])
