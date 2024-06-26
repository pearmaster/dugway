
from .runner import DugwayRunner

import typer

def run(path:str):
    tr = DugwayRunner(path)
    tr.run()


def entrypoint():
    typer.run(run)

if __name__ == '__main__':
    entrypoint()
