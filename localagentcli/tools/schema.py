"""Validate OpenAI-style function parameter JSON schemas (minimal JSON Schema subset)."""

from __future__ import annotations


def validate_function_parameters_schema(schema: object) -> list[str]:
    """Return human-readable issues; empty list means the schema is acceptable.

    Enforces a small subset suitable for tool ``parameters`` passed to models:
    top-level ``type`` must be ``\"object\"``; ``properties`` values must be
    objects with a string ``type``; ``required`` entries must name keys in
    ``properties``. Uses only the standard library.
    """
    issues: list[str] = []
    if not isinstance(schema, dict):
        issues.append("parameters schema must be a JSON object (dict)")
        return issues

    if schema.get("type") != "object":
        issues.append('top-level "type" must be the string "object"')

    properties = schema.get("properties", {})
    if properties is None:
        issues.append('"properties" must not be null')
        return issues
    if not isinstance(properties, dict):
        issues.append('"properties" must be an object mapping names to property schemas')
        return issues

    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_name, str):
            issues.append(f"property name must be a string, got {type(prop_name).__name__}")
            continue
        if not isinstance(prop_schema, dict):
            issues.append(f'property "{prop_name}" schema must be an object')
            continue
        prop_type = prop_schema.get("type")
        if not isinstance(prop_type, str) or not prop_type.strip():
            issues.append(f'property "{prop_name}" must include a non-empty string "type"')

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list):
            issues.append('"required" must be a list of strings')
        else:
            for index, name in enumerate(required):
                if not isinstance(name, str):
                    issues.append(f'"required" entry {index} must be a string')
                elif name not in properties:
                    issues.append(f'"required" lists unknown property "{name}"')

    return issues
