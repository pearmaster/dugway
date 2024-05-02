
from .runner import TestRunner

import typer

def run(path:str):
    tr = TestRunner(path)
    tr.run()


def entrypoint():
    typer.run(run)

if __name__ == '__main__':
    entrypoint()
