import pathlib
import re

import dugway.expectations

from .file import DugwayFile


def pytest_collect_file(parent, file_path: pathlib.Path) -> DugwayFile | None:
    """On collecting files, get any files that end in .dugway.yaml or .dugway.yml as dugway
    test files
    """

    pattern = r".+\.dugway\.ya?ml$"

    try:
        compiled = re.compile(pattern)
    except Exception as e:
        raise dugway.expectations.InvalidTestConfig(e) from e

    match_dugway_file = compiled.search

    if match_dugway_file(str(file_path)):
        print(f"Loading {file_path}")
        dugway_file = DugwayFile.from_parent(parent, path=file_path)
        return dugway_file

    return None
