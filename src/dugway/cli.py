
from .runner import DugwayRunner
from .reporter import MultiReporter, RichReporter, JunitReporter

import typer


def run(path:str):
    reporter = MultiReporter([RichReporter(), JunitReporter("/tmp/junit.xml")])
    tr = DugwayRunner(path, reporter)
    tr.run()

def entrypoint():
    typer.run(run)

if __name__ == '__main__':
    entrypoint()
