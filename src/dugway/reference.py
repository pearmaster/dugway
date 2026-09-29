"""Describes the installed service, converter and test step types, the options each one accepts,
and the capabilities each one is built with.

A type's summary comes from its class docstring. Its options come from its config schema, which
combines its own schema, those of the capabilities it is built with, and the generic schema for
its kind. A capability's summary comes from its class docstring too.
"""

import inspect
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .meta import JsonSchemaType
from .schema import converter_types, placeholder_instance, service_types, step_types


class Kind(str, Enum):
    services = "services"
    converters = "converters"
    steps = "steps"

    @property
    def singular(self) -> str:
        return {Kind.services: "service", Kind.converters: "converter", Kind.steps: "step"}[self]

    def types(self) -> dict[str, type]:
        return {Kind.services: service_types, Kind.converters: converter_types, Kind.steps: step_types}[self]()


@dataclass(frozen=True)
class Option:
    name: str
    type: str
    required: str
    default: str
    description: str


@dataclass(frozen=True)
class Capability:
    name: str
    summary: str


@dataclass(frozen=True)
class TypeReference:
    kind: Kind
    name: str
    summary: str
    details: str
    options: list[Option]
    exclusive_groups: list[list[str]]
    capabilities: list[Capability]


REQUIRED_MARKS = {"yes": " *", "one of": " +", "": ""}


class UnknownType(KeyError):
    """Raised when there is no service or step type with the requested name."""


def _docstring_parts(cls: type) -> tuple[str, str]:
    # Read from the class itself, since an inherited docstring describes the base class
    doc = inspect.cleandoc(cls.__dict__.get("__doc__") or "")
    summary, *details = [" ".join(paragraph.split()) for paragraph in doc.split("\n\n")]
    return summary or "No description available.", "\n\n".join(details)


def summaries(kind: Kind) -> dict[str, str]:
    """The one-line summary of every type of this kind, by type name."""
    return {name: _docstring_parts(cls)[0] for name, cls in sorted(kind.types().items())}


def _merge(first: JsonSchemaType | None, second: JsonSchemaType) -> JsonSchemaType:
    """Combines two schemas for the same property, keeping the first's keywords on conflict."""
    if not isinstance(first, dict):
        return second if first is None or first is True else first
    if not isinstance(second, dict):
        return first
    merged = {**second, **first}
    if "properties" in first or "properties" in second:
        first_props, second_props = first.get("properties", {}), second.get("properties", {})
        merged["properties"] = {
            name: _merge(first_props.get(name), second_props.get(name, True))
            for name in {**first_props, **second_props}
        }
    if "required" in first or "required" in second:
        merged["required"] = list(dict.fromkeys([*first.get("required", []), *second.get("required", [])]))
    return merged


def _merged_object_schema(schemas: list[JsonSchemaType]) -> tuple[dict[str, Any], list[list[str]]]:
    """Flattens the given object schemas into one, along with the groups of properties of
    which exactly one must be given.
    """
    merged: dict[str, Any] = {"properties": {}, "required": []}
    exclusive_groups: list[list[str]] = []

    def collect(schema: JsonSchemaType):
        if not isinstance(schema, dict):
            return
        merged.update(_merge(merged, {k: schema[k] for k in ("properties", "required") if k in schema}))
        for sub_schema in schema.get("allOf", []):
            collect(sub_schema)
        for keyword in ("oneOf", "anyOf"):
            alternatives = schema.get(keyword, [])
            for alternative in alternatives:
                # An alternative's properties are options, but only required within it
                if isinstance(alternative, dict):
                    collect({"properties": alternative.get("properties", {})})
            group = [name for alt in alternatives if isinstance(alt, dict) for name in alt.get("required", [])]
            if group:
                exclusive_groups.append(group)

    for schema in schemas:
        collect(schema)
    return merged, exclusive_groups


def _type_text(schema: JsonSchemaType) -> str:
    if not isinstance(schema, dict) or not schema.keys() - {"description", "default"}:
        return "any"
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    for keyword in ("oneOf", "anyOf"):
        if keyword in schema:
            return " or ".join(_type_text(alternative) for alternative in schema[keyword])
    type_ = schema.get("type", "any")
    if isinstance(type_, list):
        return " or ".join(type_)
    if type_ == "object" and isinstance(schema.get("additionalProperties"), dict):
        return f"object of {_type_text(schema['additionalProperties'])}"
    if type_ in ("integer", "number"):
        low, high = schema.get("minimum"), schema.get("maximum")
        if low is not None and high is not None:
            return f"{type_} {low}–{high}"
        if low is not None:
            return f"{type_} ≥ {low}"
        if high is not None:
            return f"{type_} ≤ {high}"
    return type_


def _options(schema: dict[str, Any], exclusive: set[str], prefix: str = "") -> list[Option]:
    """Lists each property, followed by its nested properties, with required ones first."""
    required = set(schema.get("required", []))
    listed: list[tuple[int, list[Option]]] = []
    for name, prop in schema.get("properties", {}).items():
        prop = prop if isinstance(prop, dict) else {}
        requirement = "yes" if name in required else "one of" if name in exclusive else ""
        option = Option(
            name=f"{prefix}{name}",
            type=_type_text(prop),
            required=requirement,
            default=json.dumps(prop["default"]) if "default" in prop else "",
            description=" ".join(prop.get("description", "").split()),
        )
        nested = _options(prop, set(), f"{prefix}{name}.") if prop.get("properties") else []
        listed.append((list(REQUIRED_MARKS).index(requirement), [option, *nested]))
    listed.sort(key=lambda entry: entry[0])
    return [option for _, options in listed for option in options]


def describe(kind: Kind, name: str) -> TypeReference:
    """Everything there is to know about one service or step type."""
    types = kind.types()
    if name not in types:
        raise UnknownType(name)
    cls = types[name]
    instance = placeholder_instance(cls, name)
    this_type = {"properties": {"type": {"const": name, "description": f"Selects this {kind.singular} type."}}}
    schema, exclusive_groups = _merged_object_schema([this_type, instance.get_config_schema()])
    exclusive = {name for group in exclusive_groups for name in group}
    summary, details = _docstring_parts(cls)
    capabilities = [Capability(cap.name, _docstring_parts(type(cap))[0]) for cap in instance.capabilities]
    return TypeReference(kind, name, summary, details, _options(schema, exclusive), exclusive_groups, capabilities)


def render_reference(ref: TypeReference) -> RenderableType:
    body = Text(ref.summary, style="bold")
    if ref.details:
        body.append("\n\n")
        body.append(ref.details, style="default")
    panel = Panel(body, title=f"[cyan]{ref.name}[/cyan] {ref.kind.singular}", title_align="left")

    table = Table(title="Options", title_justify="left", title_style="bold", expand=True)
    table.add_column("Option", no_wrap=True)
    table.add_column("Type", style="magenta", max_width=22)
    table.add_column("Default", style="green", no_wrap=True)
    table.add_column("Description", ratio=2, min_width=24)
    for option in ref.options:
        # Nested options are indented under their parent, which is always listed just above
        *parents, leaf = option.name.split(".")
        name = Text("  " * len(parents) + leaf, style="cyan")
        name.append(REQUIRED_MARKS[option.required], style="bold red")
        table.add_row(name, option.type, option.default, option.description)
    notes = ["[bold red]*[/bold red] required"]
    notes += [f"[bold red]+[/bold red] give exactly one of: {', '.join(group)}" for group in ref.exclusive_groups]
    table.caption = "\n".join(notes)
    table.caption_justify = "left"
    if not ref.capabilities:
        return Group(panel, table)

    capabilities = Table(title="Capabilities", title_justify="left", title_style="bold", expand=True)
    capabilities.add_column("Capability", style="cyan", no_wrap=True)
    capabilities.add_column("Description", ratio=1)
    for capability in ref.capabilities:
        capabilities.add_row(capability.name, capability.summary)
    return Group(panel, table, capabilities)


def render_type_list(kind: Kind) -> RenderableType:
    table = Table(title=f"Available {kind.value}", title_justify="left", title_style="bold", expand=True)
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Summary", ratio=1)
    for name, summary in summaries(kind).items():
        table.add_row(name, summary)
    return table
