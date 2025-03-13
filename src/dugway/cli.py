
from .runner import DugwayRunner
from .reporter import MultiReporter, RichReporter, JunitReporter
from .expectations import InvalidTestConfig
import typer
from typing_extensions import Annotated
from sys import exit


def run(
        path: str,
        debug: Annotated[bool, typer.Option(help="Display debug info")]=False,
):
    reporter = MultiReporter([RichReporter(debug=debug), JunitReporter("/tmp/junit.xml")])
    try:
        tr = DugwayRunner(path, reporter)
    except InvalidTestConfig as e:
        print(e)
        exit(1)
    tr.run()

def entrypoint():
    typer.run(run)

if __name__ == '__main__':
    entrypoint()
