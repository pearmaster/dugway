from pathlib import Path
from sys import exit
from typing import Annotated

import typer

from .expectations import InvalidTestConfig
from .reporter import JunitReporter, MultiReporter, RichReporter
from .runner import DugwayRunner

JUNIT_REPORT_PATH = "/tmp/junit.xml"

HELP = f"""\
Run a Dugway test suite against HTTP and MQTT services.

Dugway connects to the services defined in the suite file, runs each test case's steps in
order, and reports the result of every step as it goes.

Templates in the suite, such as [cyan]{{{{ env.MQTT_HOSTNAME }}}}[/cyan], are rendered with Jinja2
and can read environment variables through [cyan]env[/cyan].

A JUnit XML report is also written to [cyan]{JUNIT_REPORT_PATH}[/cyan].

Exit status is 0 when every test case passes, and 1 when a test fails or the suite file is
invalid.
"""

EPILOG = "Example: [cyan]dugway --debug examples/examples.yaml[/cyan]"


def run(
    path: Annotated[
        Path,
        typer.Argument(
            help="The test suite YAML file, defining its services and test cases.",
            exists=True,
            dir_okay=False,
            readable=True,
            show_default=False,
        ),
    ],
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Keep passing test cases expanded, showing each step's details. "
            "Normally only failing cases are expanded.",
        ),
    ] = False,
):
    reporter = MultiReporter([RichReporter(debug=debug), JunitReporter(JUNIT_REPORT_PATH)])
    try:
        tr = DugwayRunner(str(path), reporter)
    except InvalidTestConfig as e:
        print(e)
        exit(1)
    if not tr.run():
        exit(1)


def entrypoint():
    app = typer.Typer(
        add_completion=False,
        rich_markup_mode="rich",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    app.command(help=HELP, epilog=EPILOG)(run)
    app()


if __name__ == "__main__":
    entrypoint()
