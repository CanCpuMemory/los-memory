"""Contract versioning and the dependency-free validator for the versioned wire schemas.

The contract is versioned with semver.  A server accepts a request whose major version matches
its own and whose minor/patch is not newer; anything else is refused by name instead of being
interpreted on a best-effort basis.  Validation deliberately implements a small, explicit
keyword set (see :data:`SUPPORTED_SCHEMA_KEYWORDS`) so the layer stays dependency-free and
deterministic: a schema that uses a keyword outside that set is refused by
:func:`assert_supported_keywords` rather than silently under-validated.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .errors import (
    MissingContractVersion,
    UnsupportedContractVersion,
    UnsupportedSchemaKeyword,
    WireSchemaViolation,
)

#: Version of the application-layer contract (requests, receipts, cursors).
CONTRACT_VERSION = "1.0.0"

#: Version of the JSON Schemas that describe the wire shapes of this contract.
WIRE_SCHEMA_VERSION = "1.0.0"

SCHEMA_DIR = Path(__file__).parent / "schemas" / "v1"

SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$id",
        "$schema",
        "additionalProperties",
        "const",
        "definitions",
        "description",
        "enum",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
        "version",
        "$ref",
    }
)


def parse_version(value: str) -> Tuple[int, int, int]:
    """Parse a ``MAJOR.MINOR.PATCH`` string.

    Args:
        value: Version string.

    Returns:
        The three numeric components.

    Raises:
        UnsupportedContractVersion: If the string is not a semver triple.
    """
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(value).strip())
    if match is None:
        raise UnsupportedContractVersion(
            "declared contract_version is not a MAJOR.MINOR.PATCH triple",
            declared=str(value),
            server_version=CONTRACT_VERSION,
        )
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def check_contract_version(declared: Optional[str], server: str = CONTRACT_VERSION) -> None:
    """Refuse a request whose contract version this server cannot serve.

    The rule is deliberately asymmetric: same major and not newer than the server is accepted,
    because additive minor releases are backwards compatible for a client.

    Args:
        declared: The version the caller declared, if any.
        server: The version this server implements.

    Raises:
        MissingContractVersion: If nothing was declared.
        UnsupportedContractVersion: If the declaration cannot be served.
    """
    if declared is None or str(declared).strip() == "":
        raise MissingContractVersion(
            "requests must declare contract_version; versioning is not optional",
            server_version=server,
        )
    major_client, minor_client, patch_client = parse_version(declared)
    major_server, minor_server, patch_server = parse_version(server)
    if major_client != major_server:
        raise UnsupportedContractVersion(
            "contract major version is not supported by this server",
            declared=str(declared),
            server_version=server,
        )
    if (minor_client, patch_client) > (minor_server, patch_server):
        raise UnsupportedContractVersion(
            "contract version is newer than this server implements",
            declared=str(declared),
            server_version=server,
        )


def wire_schema_names() -> Tuple[str, ...]:
    """Return the available wire schema names in a stable order."""
    names = (path.name[: -len(".schema.json")] for path in SCHEMA_DIR.glob("*.schema.json"))
    return tuple(sorted(names))


def load_wire_schema(name: str) -> Dict[str, Any]:
    """Load one versioned wire schema by name.

    Args:
        name: Schema name without the ``.schema.json`` suffix.

    Returns:
        The parsed schema.

    Raises:
        FileNotFoundError: If the schema does not exist.
    """
    with open(SCHEMA_DIR / f"{name}.schema.json", "r", encoding="utf-8") as handle:
        schema: Dict[str, Any] = json.load(handle)
    assert_supported_keywords(schema, name)
    return schema


def assert_supported_keywords(schema: Mapping[str, Any], name: str = "<inline>") -> None:
    """Refuse a schema that uses a keyword this validator does not implement.

    Args:
        schema: A JSON Schema document.
        name: Schema name used in the error.

    Raises:
        UnsupportedSchemaKeyword: If an unknown keyword is present.
    """
    unknown = sorted(_collect_keywords(schema) - SUPPORTED_SCHEMA_KEYWORDS)
    if unknown:
        raise UnsupportedSchemaKeyword(
            "schema uses keywords this validator does not implement",
            schema=name,
            unsupported=unknown,
        )


def _collect_keywords(node: Any, names: bool = False) -> set:
    """Collect the validation keywords used in a schema document.

    Keys under ``properties`` and ``definitions`` are *names*, not keywords, so they are
    descended into without being recorded; everything else is a keyword this validator must
    implement.
    """
    found: set = set()
    if isinstance(node, Mapping):
        for key, value in node.items():
            if names:
                found |= _collect_keywords(value)
                continue
            found.add(str(key))
            if key in ("properties", "definitions"):
                found |= _collect_keywords(value, names=True)
            elif key in ("items", "additionalProperties"):
                found |= _collect_keywords(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            found |= _collect_keywords(item)
    return found


def validate_wire(data: Any, name: str) -> None:
    """Validate a payload against a named wire schema.

    Args:
        data: Payload to validate.
        name: Schema name without the ``.schema.json`` suffix.

    Raises:
        WireSchemaViolation: If the payload does not conform.
    """
    schema = load_wire_schema(name)
    errors: List[str] = []
    _validate_node(data, schema, schema, "$", errors)
    if errors:
        raise WireSchemaViolation(
            "payload does not conform to its wire schema",
            schema=name,
            errors=errors[:10],
        )


def _resolve_ref(root: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    """Resolve a local ``#/definitions/x`` reference."""
    if not ref.startswith("#/"):
        raise WireSchemaViolation("only local $ref values are supported", ref=ref)
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node  # type: ignore[no-any-return]


def _type_matches(value: Any, expected: str) -> bool:
    """Return whether ``value`` matches a JSON Schema primitive type name."""
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_node(
    value: Any, schema: Mapping[str, Any], root: Mapping[str, Any], path: str, errors: List[str]
) -> None:
    """Validate one node, appending human-readable errors."""
    if "$ref" in schema:
        _validate_node(value, _resolve_ref(root, schema["$ref"]), root, path, errors)
        return

    expected = schema.get("type")
    if expected is not None:
        candidates = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, candidate) for candidate in candidates):
            errors.append(f"{path}: expected {'/'.join(candidates)}, got {type(value).__name__}")
            return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} not in enum {schema['enum']!r}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_node(item, item_schema, root, f"{path}[{index}]", errors)

    if isinstance(value, Mapping):
        for field in schema.get("required", []):
            if field not in value:
                errors.append(f"{path}: missing required field {field!r}")
        properties = schema.get("properties", {})
        for field, property_schema in properties.items():
            if field in value:
                _validate_node(value[field], property_schema, root, f"{path}.{field}", errors)
        if schema.get("additionalProperties") is False:
            for field in value:
                if field not in properties:
                    errors.append(f"{path}: unexpected field {field!r}")


__all__ = [
    "CONTRACT_VERSION",
    "SCHEMA_DIR",
    "SUPPORTED_SCHEMA_KEYWORDS",
    "WIRE_SCHEMA_VERSION",
    "assert_supported_keywords",
    "check_contract_version",
    "load_wire_schema",
    "parse_version",
    "validate_wire",
    "wire_schema_names",
]
