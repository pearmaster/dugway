"""Loading API description documents, such as OpenAPI and AsyncAPI, that services refer to."""

import os
from typing import Any
from urllib.parse import urlsplit

from .expectations import InvalidTestConfig
from .runner import load_suite_document


def load_api_document(runner, spec_path: str, kind: str, version_field: str, major_versions: tuple[str, ...]) -> Any:
    """Loads the document at spec_path, relative to the suite file, with its $refs resolved.

    Raises InvalidTestConfig if it can't be loaded, or if its version field doesn't start with one
    of the given major versions, such as "3.".
    """
    if not urlsplit(spec_path).scheme:
        spec_path = os.path.join(getattr(runner, "suite_directory", ""), spec_path)
    try:
        spec = load_suite_document(spec_path)
    except Exception as e:  # whatever went wrong, the suite can't be run
        raise InvalidTestConfig(f"Could not load the {kind} document {spec_path}: {e}") from e
    version = str(spec.get(version_field, "")) if isinstance(spec, dict) else ""
    if not version.startswith(major_versions):
        supported = " or ".join(v.rstrip(".") for v in major_versions)
        raise InvalidTestConfig(
            f"{spec_path} is not an {kind} {supported} document (its '{version_field}' field is '{version}')"
        )
    return spec
