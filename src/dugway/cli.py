
from .runner import DugwayRunner
from .reporter import MultiReporter, RichReporter, JunitReporter
from .expectations import InvalidTestConfig
import typer
from sys import exit


def run(path:str):
    reporter = MultiReporter([RichReporter(), JunitReporter("/tmp/junit.xml")])
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
