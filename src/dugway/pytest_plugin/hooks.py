import logging
import logging.config
from typing import Optional
import os
import re
import pathlib

import pytest

from .file import DugwayFile
import dugway.expectations


def pytest_collect_file(parent, path: os.PathLike) -> Optional[DugwayFile]:
    """On collecting files, get any files that end in .dugway.yaml or .dugway.yml as dugway
    test files
    """

    pattern = r".+\.dugway\.ya?ml$"

    try:
        compiled = re.compile(pattern)
    except Exception as e:
        raise dugway.expectations.InvalidTestConfig(e) from e

    match_dugway_file = compiled.search

    path = pathlib.Path(path)

    if match_dugway_file(str(path)):
        print(f"Loading {path}")
        dugway_file = DugwayFile.from_parent(parent, path=path)
        return dugway_file

    return None

