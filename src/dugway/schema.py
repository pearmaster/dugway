"""Builds the complete JSON Schema for a Dugway test suite file.

Each service and test step type only reveals its schema from an instance, so a placeholder
instance of every registered type is built, with config validation switched off, and its
schema is included for suite entries of that type.
"""

from typing import Any

from jacobsjsonschema.draft7 import JsonSchemaValidationError
from jacobsjsonschema.draft7 import Validator as JsonSchemaValidator
from stevedore import ExtensionManager

from .builtin_steps import BUILTIN_STEPS
from .case import TestCase
from .expectations import InvalidTestConfig
from .meta import JsonSchemaType, without_config_validation
from .runner import TestSuite, load_suite_document
from .service import Service
from .step import TestStep

SCHEMA_DIALECT = "http://json-schema.org/draft-07/schema#"


class _PlaceholderRunner:
    """Stands in for a DugwayRunner while building placeholder objects."""

    def template_eval(self, element: Any, context: dict[str, Any] | None = None) -> Any:
        return element


def _registered_types(namespace: str) -> dict[str, type]:
    manager = ExtensionManager(namespace=namespace, invoke_on_load=False)
    return {ext.name: ext.plugin for ext in manager}


def _type_schema(cls: type, type_name: str) -> JsonSchemaType:
    with without_config_validation():
        placeholder = cls(runner=_PlaceholderRunner(), config={"type": type_name})
    return placeholder.get_config_schema()


def _schema_by_type(types: dict[str, type], generic: JsonSchemaType) -> JsonSchemaType:
    """Allows an object of any of the given types, checked against that type's schema."""
    type_names = sorted(types)
    return {
        "allOf": [
            generic,
            {"properties": {"type": {"enum": type_names}}},
            *(
                {
                    "if": {"properties": {"type": {"const": name}}},
                    "then": _type_schema(types[name], name),
                }
                for name in type_names
            ),
        ]
    }


def build_suite_schema() -> dict[str, Any]:
    services = _registered_types("dugwayservice")
    steps = {**_registered_types("dugwayteststep"), **BUILTIN_STEPS}
    step_schema = _schema_by_type(steps, TestStep.get_generic_schema())
    case_schema = {
        "allOf": [
            TestCase.get_generic_schema(),
            {"properties": {"steps": {"items": step_schema}}},
        ]
    }
    suite = TestSuite.get_generic_schema()
    return {
        "$schema": SCHEMA_DIALECT,
        "title": "Dugway test suite",
        **suite,
        "properties": {
            **suite["properties"],
            "services": {
                "type": "object",
                "additionalProperties": _schema_by_type(services, Service.get_generic_schema()),
            },
            "testCases": {"type": "object", "additionalProperties": case_schema},
            "caseSetUp": case_schema,
            "caseTearDown": case_schema,
        },
    }


def validate_suite_file(filename: str) -> None:
    """Raises InvalidTestConfig if the suite file does not comply with the suite schema."""
    validator = JsonSchemaValidator(build_suite_schema())
    try:
        validator.validate(load_suite_document(filename))
    except JsonSchemaValidationError as e:
        raise InvalidTestConfig(str(e)) from e
