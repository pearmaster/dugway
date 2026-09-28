from sys import exit
from typing import Annotated

import typer

from .expectations import InvalidTestConfig
from .reporter import JunitReporter, MultiReporter, RichReporter
from .runner import DugwayRunner


def run(
    path: str,
    debug: Annotated[bool, typer.Option(help="Display debug info")] = False,
):
    reporter = MultiReporter([RichReporter(debug=debug), JunitReporter("/tmp/junit.xml")])
    try:
        tr = DugwayRunner(path, reporter)
    except InvalidTestConfig as e:
        print(e)
        exit(1)
    if not tr.run():
        exit(1)


def entrypoint():
    typer.run(run)


if __name__ == "__main__":
    entrypoint()
