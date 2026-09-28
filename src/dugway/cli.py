import inspect
import json
import sys
from pathlib import Path
from sys import exit
from typing import Annotated

import questionary
import typer
from rich.console import Console

from .expectations import InvalidTestConfig
from .reference import Kind, UnknownType, describe, render_reference, render_type_list, summaries
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


def _unwrap_paragraphs(func):
    """Joins the lines of each docstring paragraph, since rich help keeps line breaks as written."""
    paragraphs = inspect.cleandoc(func.__doc__).split("\n\n")
    func.__doc__ = "\n\n".join(" ".join(paragraph.split()) for paragraph in paragraphs)
    return func


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
@_unwrap_paragraphs
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
@_unwrap_paragraphs
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
@_unwrap_paragraphs
def schema():
    """Print the JSON Schema that test suite files must comply with.

    It covers every installed service and test step type, and can be given to an editor to
    check and complete suite files as you write them.
    """
    typer.echo(json.dumps(build_suite_schema(), indent=2))


def _browse(console: Console, kind: Kind | None):
    """Lets the user pick types from menus, and shows each one picked, until they stop."""
    while True:
        chosen_kind = (
            kind
            or questionary.select(
                "What would you like help with?",
                choices=[
                    questionary.Choice("Services", Kind.services, description="Connections to the systems under test"),
                    questionary.Choice(
                        "Steps", Kind.steps, description="The actions and checks a test case is made of"
                    ),
                ],
            ).ask()
        )
        if chosen_kind is None:
            return
        name = questionary.select(
            f"Pick a {chosen_kind.singular}:",
            choices=[questionary.Choice(n, n, description=s) for n, s in summaries(chosen_kind).items()],
            use_search_filter=True,
            use_jk_keys=False,
            instruction="(arrow keys to move, type to filter)",
        ).ask()
        if name is None:
            return
        console.print(render_reference(describe(chosen_kind, name)))
        if not questionary.confirm("Look up another?", default=False).ask():
            return


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@app.command(
    "help",
    epilog="Examples: [cyan]dugway help[/cyan], [cyan]dugway help steps[/cyan], "
    "[cyan]dugway help services mqtt[/cyan]",
)
@_unwrap_paragraphs
def help_(
    kind: Annotated[
        Kind | None,
        typer.Argument(help="Which kind of type to look up.", show_default=False),
    ] = None,
    name: Annotated[
        str | None,
        typer.Argument(
            help="The type's name, as given for 'type' in a suite file.",
            show_default=False,
        ),
    ] = None,
):
    """Browse the service and test step types, and the options each one accepts.

    With no arguments, pick services or steps and then a type from menus, and its summary and
    options are shown. Give the kind, or the kind and a type name, to skip those menus.

    When not run in a terminal, the available types are listed instead of prompting.
    """
    console = Console()
    if kind is not None and name is not None:
        try:
            console.print(render_reference(describe(kind, name)))
        except UnknownType:
            raise typer.BadParameter(
                f"there is no {kind.singular} type named '{name}'. " f"Choose from: {', '.join(summaries(kind))}",
                param_hint="'NAME'",
            ) from None
    elif _is_interactive():
        _browse(console, kind)
    else:
        for listed_kind in [kind] if kind else list(Kind):
            console.print(render_type_list(listed_kind))


def entrypoint():
    app()


if __name__ == "__main__":
    entrypoint()
