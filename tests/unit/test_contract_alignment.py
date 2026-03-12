"""Contract alignment regression tests."""
from __future__ import annotations

import json
from pathlib import Path

from memory_tool.output import success
from memory_tool.schema import SCHEMA_VERSION, SCHEMA_DIR


def test_success_response_preserves_extra_meta_fields() -> None:
    """Ensure success(..., **extra_meta) is not dropped."""
    response = success(
        data={"ok": True},
        profile="codex",
        route_mode="chat_path",
        run_id="run-123",
    )

    payload = response.to_dict()
    assert payload["meta"]["profile"] == "codex"
    assert payload["meta"]["route_mode"] == "chat_path"
    assert payload["meta"]["run_id"] == "run-123"


def test_schema_module_version_matches_schema_files() -> None:
    """Keep schema package version aligned with schema file versions."""
    schema_versions = set()
    for schema_file in Path(SCHEMA_DIR).glob("*.schema.json"):
        data = json.loads(schema_file.read_text(encoding="utf-8"))
        if "version" in data:
            schema_versions.add(data["version"])

    assert SCHEMA_VERSION in schema_versions


def test_observation_kind_contract_is_extensible() -> None:
    """Observation kind should allow custom non-empty values."""
    base_schema = json.loads((Path(SCHEMA_DIR) / "base.schema.json").read_text(encoding="utf-8"))
    observation_kind = base_schema["definitions"]["observation_kind"]

    assert observation_kind["type"] == "string"
    assert observation_kind["minLength"] == 1
    assert "enum" not in observation_kind
