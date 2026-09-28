import json
from pathlib import Path
from sys import exit
from typing import Annotated

import typer

from .expectations import InvalidTestConfig
from .reporter import JunitReporter, MultiReporter, RichReporter
from .runner import DugwayRunner
from .schema import build_suite_schema, validate_suite_file

app = typer.Typer(
    help="Dugway API testing framework, for testing HTTP and MQTT services.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

YamlFiles = Annotated[
    list[Path],
    typer.Argument(
        help="One or more test suite YAML files, each defining its services and test cases.",
        exists=True,
        dir_okay=False,
        readable=True,
        show_default=False,
    ),
]


@app.command(
    epilog="Example: [cyan]dugway run --junit results.xml tests/*.yaml[/cyan]",
)
def run(
    yaml_files: YamlFiles,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Keep passing test cases expanded, showing each step's details. "
            "Normally only failing cases are expanded.",
        ),
    ] = False,
    junit: Annotated[
        Path | None,
        typer.Option(
            help="Also write a JUnit XML report of the results to this file.",
            metavar="FILE",
            show_default=False,
            dir_okay=False,
            writable=True,
        ),
    ] = None,
):
    """Run test suites against their services.

    Dugway connects to the services defined in each suite file, runs each test case's steps in
    order, and reports the result of every step as it goes. Suites run one after another, each
    with its own services.

    Every suite file is loaded before any are run. An invalid file is reported and skipped,
    and the remaining suites still run.

    Exit status is 0 when every test case in every suite passes, and 1 when a test fails or a
    suite file is invalid.
    """
    reporters = [RichReporter(debug=debug)]
    if junit is not None:
        reporters.append(JunitReporter(junit))
    reporter = MultiReporter(reporters)
    all_passed = True
    runners = []
    for yaml_file in yaml_files:
        try:
            runners.append(DugwayRunner(str(yaml_file), reporter))
        except InvalidTestConfig as e:
            typer.echo(f"{yaml_file} is invalid: {e}", err=True)
            all_passed = False
    for runner in runners:
        if not runner.run():
            all_passed = False
    if not all_passed:
        exit(1)


@app.command()
def validate(yaml_files: YamlFiles):
    """Check that test suite files comply with the schema, without running them.

    Every file is checked, and each is reported as valid or invalid.

    Exit status is 0 when every file is valid, and 1 when any is not.
    """
    all_valid = True
    for yaml_file in yaml_files:
        try:
            validate_suite_file(str(yaml_file))
        except InvalidTestConfig as e:
            typer.echo(f"{yaml_file} is invalid: {e}", err=True)
            all_valid = False
        else:
            typer.echo(f"{yaml_file} is valid")
    if not all_valid:
        exit(1)


@app.command(
    epilog="Example: [cyan]dugway schema > dugway.schema.json[/cyan]",
)
def schema():
    """Print the JSON Schema that test suite files must comply with.

    It covers every installed service and test step type, and can be given to an editor to
    check and complete suite files as you write them.
    """
    typer.echo(json.dumps(build_suite_schema(), indent=2))


def entrypoint():
    app()


if __name__ == "__main__":
    entrypoint()
