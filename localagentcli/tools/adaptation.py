"""Model-aware tool definition adaptation for agent turns."""

from __future__ import annotations

from localagentcli.models.model_info import ModelInfo
from localagentcli.tools.base import Tool


def adapt_tool_definitions(tools: list[Tool], model_info: ModelInfo) -> list[dict]:
    """Return model-facing tool definitions adapted for the active model.

    Rules:
    - If the model does not advertise tool use, no tools are exposed.
    - Tools can declare required model capabilities.
    - Tools can declare a minimum `default_max_tokens` budget.
    """
    capabilities = model_info.capabilities if isinstance(model_info.capabilities, dict) else {}
    if capabilities.get("tool_use") is False:
        return []

    default_max_tokens = _coerce_positive_int(model_info.default_max_tokens, 4096)
    definitions: list[dict] = []

    for tool in tools:
        if default_max_tokens < tool.minimum_model_default_max_tokens:
            continue
        required = tool.required_model_capabilities
        if required and not _has_capabilities(capabilities, required):
            continue
        definitions.append(tool.definition())

    return definitions


def _has_capabilities(capabilities: dict, required: tuple[str, ...]) -> bool:
    return all(bool(capabilities.get(capability, False)) for capability in required)


def _coerce_positive_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, float):
        parsed = int(value)
        return parsed if parsed > 0 else default
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return default
        return parsed if parsed > 0 else default
    return default
