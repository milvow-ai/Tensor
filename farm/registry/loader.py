"""Load ``config/registry.yaml`` into a validated :class:`~farm.registry.models.Registry`.

Failures raise :class:`RegistryError` with the file name and one readable line per problem
(``providers.reoon.connections.0.auth_ref: ...``). Rejected input values are never echoed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from farm.registry.models import Registry

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


class RegistryError(ValueError):
    """The registry file could not be read, parsed or validated."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (plain PyYAML silently keeps the last one, so two
    ``reoon:`` blocks would drop the first without a word)."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"found duplicate key {key!r}",
                        key_node.start_mark,
                    )
                seen.add(key)
            except TypeError:  # unhashable key: let SafeLoader report it
                break
        return super().construct_mapping(node, deep=deep)


def _format_validation_error(source: str, exc: ValidationError) -> str:
    lines = [f"invalid registry {source}: {exc.error_count()} error(s)"]
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        message = str(err["msg"]).removeprefix("Value error, ")
        indented = message.replace("\n", "\n      ")
        lines.append(f"  - {location}: {indented}")
    return "\n".join(lines)


def parse_registry(text: str, *, source: str = "<string>") -> Registry:
    """Parse and validate registry YAML text."""
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
        problem = getattr(exc, "problem", None) or "invalid YAML"
        raise RegistryError(f"cannot parse registry {source}{where}: {problem}") from None
    if not isinstance(data, dict):
        raise RegistryError(f"invalid registry {source}: the top level must be a mapping")
    try:
        return Registry.model_validate(data)
    except ValidationError as exc:
        raise RegistryError(_format_validation_error(source, exc)) from None


def load_registry(path: str | Path) -> Registry:
    """Read and validate a registry YAML file."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(
            f"cannot read registry file {file}: {exc.strerror or type(exc).__name__}"
        ) from None
    return parse_registry(text, source=str(file))


def export_json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) for the registry; ProviderSpec and ConnectionSpec live in ``$defs``."""
    schema = Registry.model_json_schema(mode="validation")
    schema["$schema"] = JSON_SCHEMA_DIALECT
    schema["title"] = "Harness Farm registry"
    return schema
