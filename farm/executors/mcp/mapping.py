"""Config-driven tool to capability mapping for MCP executors.

Transforms capability request parameters to MCP tool arguments,
and maps MCP tool results back to ExecResult data fields.
Supports jinja-free field mapping and dot/bracket path extraction.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

_PATH_TOKEN_RE = re.compile(r"[^.\[\]]+")
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_.-]+)\}")


class ToolMapping(BaseModel):
    """Specification for mapping a capability to an MCP tool."""

    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


def get_capability_mapping(
    tool_map: dict[str, Any] | None, capability: str
) -> ToolMapping | None:
    """Look up a capability in the tool_map configuration."""
    if not tool_map or capability not in tool_map:
        return None
    entry = tool_map[capability]
    if isinstance(entry, ToolMapping):
        return entry
    if isinstance(entry, dict):
        return ToolMapping(**entry)
    if isinstance(entry, str):
        return ToolMapping(tool=entry)
    return None


def extract_value(data: Any, path: str) -> Any:
    """Extract a nested value from dict or list using dot/bracket path syntax."""
    if data is None or not path:
        return None

    # Direct key lookup first
    if isinstance(data, dict) and path in data:
        return data[path]

    tokens = _PATH_TOKEN_RE.findall(path)
    current: Any = data
    for token in tokens:
        if current is None:
            return None
        if isinstance(current, dict):
            if token in current:
                current = current[token]
            elif token.isdigit() and int(token) in current:
                current = current[int(token)]
            else:
                return None
        elif isinstance(current, (list, tuple)):
            if token.isdigit():
                idx = int(token)
                if 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                return None
        else:
            return None
    return current


def render_param_template(template: Any, params: dict[str, Any]) -> Any:
    """Render a parameter mapping specification against input params."""
    if isinstance(template, str):
        # Exact match with input param key -> preserve full type
        if template in params:
            return params[template]

        # Check for {placeholder} markers
        if "{" in template and "}" in template:
            def _replace(match: re.Match[str]) -> str:
                var_name = match.group(1)
                val = extract_value(params, var_name)
                return str(val) if val is not None else ""

            return _PLACEHOLDER_RE.sub(_replace, template)

        # Literal string
        return template

    if isinstance(template, dict):
        return {k: render_param_template(v, params) for k, v in template.items()}

    if isinstance(template, list):
        return [render_param_template(item, params) for item in template]

    return template


def map_request_params(
    param_mapping: dict[str, Any] | None, input_params: dict[str, Any]
) -> dict[str, Any]:
    """Map capability input params to MCP tool arguments."""
    if not param_mapping:
        return dict(input_params)

    mapped: dict[str, Any] = {}
    for target_field, spec in param_mapping.items():
        val = render_param_template(spec, input_params)
        if val is not None:
            mapped[target_field] = val
    return mapped


def map_tool_result(result_mapping: dict[str, Any] | None, raw_data: Any) -> Any:
    """Map MCP tool result to capability result data."""
    if not result_mapping:
        if isinstance(raw_data, dict):
            return raw_data
        return raw_data

    mapped: dict[str, Any] = {}
    for target_field, spec in result_mapping.items():
        if isinstance(spec, str):
            val = extract_value(raw_data, spec)
            if val is not None:
                mapped[target_field] = val
        elif isinstance(spec, dict):
            mapped[target_field] = map_tool_result(spec, raw_data)
        else:
            mapped[target_field] = spec
    return mapped
