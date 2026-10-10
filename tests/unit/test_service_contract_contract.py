"""Contract-layer tests: versioning, schema conformance, atomicity, storage declaration.

These complement ``test_service_contract_negative.py`` (N1-N11).  They check the properties that
make the negative tests meaningful: that the wire schemas are versioned and actually enforced,
that ``accepted`` is only ever returned for a fully committed write, that the declared storage
schema has not drifted from the design document, and that nothing here is reachable from the
shipped CLI.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from memory_tool.service_contract import (
    CONTRACT_VERSION,
    SERVICE_DB_FILENAME,
    STORAGE_DDL,
    STORAGE_PRAGMAS,
    STORAGE_SCHEMA_ADDITIONS,
    STORAGE_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_KEYWORDS,
    WIRE_SCHEMA_VERSION,
    Action,
    ClientOutbox,
    ContractError,
    EventSubmission,
    Face,
    FaceRequest,
    IdempotencyConflict,
    IdentityFieldRejected,
    InProcessAdapter,
    MemoryProposal,
    MissingContractVersion,
    OfflineWriteNotAccepted,
    ProjectRegistry,
    ReceiptState,
    Registration,
    RequestedScope,
    ScopeNotGranted,
    UnsupportedContractVersion,
    UnsupportedSchemaKeyword,
    WireSchemaViolation,
    assert_supported_keywords,
    check_contract_version,
    declared_columns,
    declared_tables,
    load_wire_schema,
    make_queued_receipt,
    payload_hash,
    validate_wire,
    wire_schema_names,
)

ROOT = Path(__file__).resolve().parents[2]
DESIGN_DOC = ROOT / "docs" / "design" / "p2-write-path-minimal-loop.md"

EXPECTED_SCHEMAS = frozenset(
    {"error", "event-submission", "event-view", "face-result", "memory-proposal", "receipt"}
)


def _lab(svc: InProcessAdapter, actions: Tuple[Action, ...] = (Action.READ,)) -> Registration:
    """Register one credential of ``owner-a`` with the named actions on ``space-a/proj-a``."""
    registration = svc.register_device(
        "owner-a", "device-m1", key_id="key-a", actor_agent_id="agent-1"
    )
    for action in actions:
        svc.grant("owner-a", "space-a", "proj-a", action)
    return registration


def _full_lab(svc: InProcessAdapter) -> Registration:
    """Register one credential holding every action on ``space-a/proj-a``."""
    return _lab(svc, tuple(Action))


def _submission(**overrides: Any) -> EventSubmission:
    payload: Dict[str, Any] = {
        "client_event_id": "ce-1",
        "content": "hello",
        "source_instance": "device-m1",
        "thread_id": "th-1",
        "source_seq": 1,
        "role": "user",
        "observed_at": "2026-10-11T00:00:00Z",
        "source_app": "codex",
        "project_id": "proj-a",
        "requested_scope": RequestedScope(projects=("proj-a",)),
        "contract_version": CONTRACT_VERSION,
    }
    payload.update(overrides)
    return EventSubmission(**payload)


def _proposal(**overrides: Any) -> MemoryProposal:
    payload: Dict[str, Any] = {
        "kind": "decision",
        "content": "a decision",
        "project_id": "proj-a",
        "requested_scope": RequestedScope(projects=("proj-a",)),
    }
    payload.update(overrides)
    return MemoryProposal(**payload)


# -------------------------------------------------------------------------- version policy


def test_contract_version_is_semver_and_enforced() -> None:
    check_contract_version(CONTRACT_VERSION)
    check_contract_version("1.0.0", server="1.1.0")
    check_contract_version("1.1.0", server="1.1.0")

    for declared, expected in [
        (None, MissingContractVersion),
        ("", MissingContractVersion),
        ("2.0.0", UnsupportedContractVersion),
        ("1.9.0", UnsupportedContractVersion),
        ("not-a-version", UnsupportedContractVersion),
    ]:
        with pytest.raises(expected):
            check_contract_version(declared)

    with pytest.raises(UnsupportedContractVersion):
        check_contract_version("1.1.0", server="1.0.0")


def test_adapter_refuses_an_unserviceable_contract_version() -> None:
    svc = InProcessAdapter()
    reg = _full_lab(svc)

    with pytest.raises(UnsupportedContractVersion):
        svc.append_event(reg.credential, _submission(contract_version="2.0.0"))

    with pytest.raises(MissingContractVersion):
        svc.append_event(reg.credential, _submission(contract_version=""))

    assert svc.stats()["events"] == 0


# ------------------------------------------------------------------------- wire schemas


def test_wire_schemas_are_versioned_and_pinned() -> None:
    assert set(wire_schema_names()) == EXPECTED_SCHEMAS

    for name in wire_schema_names():
        schema = load_wire_schema(name)
        assert schema["version"] == WIRE_SCHEMA_VERSION, name
        assert schema["$schema"] == "http://json-schema.org/draft-07/schema#", name
        assert schema["type"] == "object", name
        assert schema["additionalProperties"] is False, name
        assert_supported_keywords(schema, name)


def test_unsupported_schema_keywords_are_refused_not_ignored() -> None:
    """A schema may not rely on a constraint this validator would silently skip."""
    with pytest.raises(UnsupportedSchemaKeyword) as excinfo:
        assert_supported_keywords(
            {
                "type": "object",
                "properties": {"a": {"type": "string", "anyOf": [{"const": "x"}]}},
            },
            "inline",
        )
    assert "anyOf" in excinfo.value.details["unsupported"]


def test_supported_keyword_set_is_the_documented_one() -> None:
    assert "anyOf" not in SUPPORTED_SCHEMA_KEYWORDS
    assert {"type", "properties", "required", "additionalProperties", "enum", "$ref"} <= (
        SUPPORTED_SCHEMA_KEYWORDS
    )


def test_requests_and_responses_conform_to_their_wire_schemas() -> None:
    svc = InProcessAdapter()
    reg = _full_lab(svc)

    validate_wire(_submission().to_wire(), "event-submission")
    validate_wire(_proposal().to_wire(), "memory-proposal")

    event_receipt = svc.append_event(reg.credential, _submission())
    validate_wire(event_receipt.to_wire(), "receipt")
    validate_wire(svc.get_event(reg.credential, "ce-1").to_wire(), "event-view")

    memory_receipt = svc.propose_memory(reg.credential, _proposal())
    validate_wire(memory_receipt.to_wire(), "receipt")

    for face in Face:
        result = svc.read_face(reg.credential, face, FaceRequest(query="decision"))
        validate_wire(result.to_wire(), "face-result")


def test_refusals_conform_to_the_error_schema() -> None:
    svc = InProcessAdapter()
    reg = _full_lab(svc)
    svc.append_event(reg.credential, _submission(content="first"))

    with pytest.raises(IdempotencyConflict) as excinfo:
        svc.append_event(reg.credential, _submission(content="second"))
    validate_wire(excinfo.value.to_wire(), "error")

    with pytest.raises(ScopeNotGranted) as excinfo:
        svc.read_face(
            reg.credential,
            Face.SEARCH,
            FaceRequest(requested_scope=RequestedScope(projects=("proj-zzz",))),
        )
    validate_wire(excinfo.value.to_wire(), "error")


def test_every_error_code_is_declared_in_the_error_schema() -> None:
    """The schema cannot silently lag behind the refusals the layer can produce."""
    declared = set(load_wire_schema("error")["properties"]["error"]["properties"]["code"]["enum"])

    def walk(cls: type) -> List[type]:
        found = [cls]
        for child in cls.__subclasses__():
            found.extend(walk(child))
        return found

    codes = {item.code for item in walk(ContractError)}
    assert codes - {"contract_error"} == declared


def test_wire_validation_refuses_unknown_fields_and_bad_types() -> None:
    payload = _submission().to_wire()
    validate_wire(payload, "event-submission")

    with pytest.raises(WireSchemaViolation) as excinfo:
        validate_wire({**payload, "unexpected": 1}, "event-submission")
    assert any("unexpected field" in item for item in excinfo.value.details["errors"])

    with pytest.raises(WireSchemaViolation):
        validate_wire({**payload, "source_seq": "one"}, "event-submission")


def test_server_owned_fields_are_refused_before_schema_validation() -> None:
    """The refusal must name the identity violation, not a generic unexpected field."""
    payload = {**_submission().to_wire(), "owner_id": "owner-b"}
    with pytest.raises(IdentityFieldRejected) as excinfo:
        EventSubmission.from_wire(payload)
    assert excinfo.value.details["field"] == "owner_id"


def test_every_wire_rendering_is_json_serializable() -> None:
    """Whatever the layer hands a transport must survive ``json.dumps`` without coercion."""
    svc = InProcessAdapter()
    reg = _full_lab(svc)
    receipt = svc.append_event(reg.credential, _submission())
    memory = svc.propose_memory(reg.credential, _proposal())
    scope = svc.effective_scope(reg.credential, FaceRequest())
    tombstone = svc.tombstone_for(
        svc.retract_memory(reg.credential, memory.memory_id, reason="no longer true").memory_id
    )
    manifest = svc.erase_memory(reg.credential, memory.memory_id)
    cursor = svc.changelog_cursor(reg.credential)

    renderings = [
        _submission().to_wire(),
        _proposal().to_wire(),
        receipt.to_wire(),
        memory.to_wire(),
        svc.get_event(reg.credential, "ce-1").to_wire(),
        svc.read_face(reg.credential, Face.SEARCH, FaceRequest()).to_wire(),
        scope.to_wire(),
        RequestedScope(projects=("proj-a",)).to_wire(),
        tombstone.to_wire(),
        manifest.to_wire(),
        svc.replay_erasure(manifest).to_wire(),
        svc.read_changelog(reg.credential, cursor).to_wire(),
        ProjectRegistry().to_wire(),
        make_queued_receipt(
            principal_key_id="key-a",
            owner_id="owner-a",
            client_event_id="ce-queued",
            payload_hash=payload_hash({"queued": True}),
            created_at="2026-10-11T00:00:00Z",
        ).to_wire(),
        svc.stats(),
    ]

    for rendering in renderings:
        assert json.loads(json.dumps(rendering)) is not None


def test_shared_inline_definitions_do_not_drift() -> None:
    event = load_wire_schema("event-submission")["definitions"]
    proposal = load_wire_schema("memory-proposal")["definitions"]
    receipt = load_wire_schema("receipt")["definitions"]
    face = load_wire_schema("face-result")["definitions"]

    assert event["requested_scope"] == proposal["requested_scope"]
    assert event["evidence_link"] == proposal["evidence_link"]
    assert receipt["degradation"] == face["degradation"]


# ------------------------------------------------------------------------------ atomicity


def test_accept_is_atomic_when_a_write_fails_mid_commit() -> None:
    def injector(stage: str) -> None:
        if stage == "after_event_write":
            raise RuntimeError("crash between the event and its receipt")

    svc = InProcessAdapter(fault_injector=injector)
    reg = _lab(svc, (Action.EVENT_APPEND,))

    with pytest.raises(RuntimeError):
        svc.append_event(reg.credential, _submission())

    assert svc.stats()["events"] == 0
    assert svc.stats()["receipts"] == 0
    assert svc.stats()["outbox"] == 0
    assert svc.receipt_for(reg.credential, "ce-1") is None


def test_counters_roll_back_so_a_retry_reuses_the_sequence() -> None:
    def injector(stage: str) -> None:
        if stage == "before_commit":
            raise RuntimeError("crash before the commit")

    svc = InProcessAdapter(fault_injector=injector)
    reg = _lab(svc, (Action.EVENT_APPEND,))

    with pytest.raises(RuntimeError):
        svc.append_event(reg.credential, _submission())

    svc._fault_injector = None  # the injected crash clears on retry
    receipt = svc.append_event(reg.credential, _submission())
    assert receipt.server_seq == 1
    assert receipt.replayed is False


def test_lost_response_after_commit_is_recovered_by_replay() -> None:
    """A fault past the commit boundary must not produce a second commit on retry."""
    state = {"fault": True}

    def injector(stage: str) -> None:
        if stage == "after_commit" and state["fault"]:
            raise TimeoutError("response lost after the commit")

    svc = InProcessAdapter(fault_injector=injector)
    reg = _lab(svc, (Action.EVENT_APPEND,))

    with pytest.raises(TimeoutError):
        svc.append_event(reg.credential, _submission())
    assert svc.stats()["events"] == 1, "the write is durable even though the answer was lost"

    state["fault"] = False
    retry = svc.append_event(reg.credential, _submission())
    assert retry.replayed is True
    assert retry.server_seq == 1
    assert svc.stats()["events"] == 1


def test_memory_writes_are_atomic_too() -> None:
    def injector(stage: str) -> None:
        if stage == "before_commit":
            raise RuntimeError("crash before the commit")

    svc = InProcessAdapter(fault_injector=injector)
    reg = _lab(svc, (Action.MEMORY_PROPOSE,))

    with pytest.raises(RuntimeError):
        svc.propose_memory(reg.credential, _proposal())

    assert svc.stats()["memory_units"] == 0
    assert svc.stats()["revisions"] == 0


# ------------------------------------------------------------------- client-side receipts


def test_a_client_receipt_is_only_ever_queued() -> None:
    receipt = make_queued_receipt(
        principal_key_id="key-a",
        owner_id="owner-a",
        client_event_id="ce-1",
        payload_hash=payload_hash({"any": "payload"}),
        created_at="2026-10-11T00:00:00Z",
    )
    assert receipt.state is ReceiptState.QUEUED
    assert receipt.server_seq is None
    validate_wire(receipt.to_wire(), "receipt")

    outbox = ClientOutbox()
    outbox.enqueue(_submission())
    with pytest.raises(OfflineWriteNotAccepted):
        outbox.acknowledge(receipt)
    assert outbox.state_of("ce-1").value == "queued"


def test_receipt_states_are_exactly_two() -> None:
    assert {state.value for state in ReceiptState} == {"accepted", "queued"}


# --------------------------------------------------------------- storage schema declaration


def _design_sql_block() -> str:
    text = DESIGN_DOC.read_text(encoding="utf-8")
    start = text.index("```sql")
    end = text.index("```", start + len("```sql"))
    return text[start + len("```sql") : end]


def test_storage_declaration_covers_the_designed_schema() -> None:
    designed = declared_columns(_design_sql_block())
    declared = declared_tables()

    assert designed, "the design document's SQL block could not be parsed"

    for table, columns in designed.items():
        assert table in declared, f"{table} is designed but not declared"
        missing = [column for column in columns if column not in declared[table]]
        assert not missing, f"{table} lost designed columns: {missing}"
        registered = STORAGE_SCHEMA_ADDITIONS.get(table, {})
        for column in declared[table]:
            if column in columns:
                continue
            assert registered.get(column), (
                f"{table}.{column} is declared but neither designed nor registered in "
                "STORAGE_SCHEMA_ADDITIONS"
            )

    for table in declared:
        if table in designed:
            continue
        assert STORAGE_SCHEMA_ADDITIONS.get(table), (
            f"{table} is declared but neither designed nor registered"
        )

    for table, additions in STORAGE_SCHEMA_ADDITIONS.items():
        for column, reason in additions.items():
            assert reason.strip(), f"{table}.{column} needs a reason"
            assert column in declared[table], f"{table}.{column} is registered but not declared"


def test_designed_schema_version_is_pinned() -> None:
    assert STORAGE_SCHEMA_VERSION == 1
    assert SERVICE_DB_FILENAME == "service.sqlite3"
    assert STORAGE_PRAGMAS == ("PRAGMA foreign_keys=ON", "PRAGMA journal_mode=WAL")


def test_declared_ddl_is_executable_sql() -> None:
    """The declaration is checked for SQL validity in a throwaway in-memory database."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(STORAGE_PRAGMAS[0])
        for statement in STORAGE_DDL:
            connection.execute(statement)
        created = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert created == set(declared_tables())
        for table, columns in declared_tables().items():
            actual = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            assert actual == list(columns), table
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_using_the_adapter_creates_no_file(tmp_path: Path) -> None:
    svc = InProcessAdapter()
    reg = _lab(svc, tuple(Action))
    svc.append_event(reg.credential, _submission())
    svc.propose_memory(reg.credential, _proposal())
    svc.read_face(reg.credential, Face.SEARCH, FaceRequest(query="decision"))
    json.dumps(svc.export_snapshot())

    assert list(tmp_path.iterdir()) == []
    assert not (ROOT / SERVICE_DB_FILENAME).exists()


# ------------------------------------------------------------------- zero runtime risk


def _run_import_probe(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_importing_the_tool_does_not_import_the_contract_layer() -> None:
    output = _run_import_probe(
        "import sys, memory_tool, memory_tool.cli;"
        " assert 'memory_tool.service_contract' not in sys.modules, sorted(sys.modules);"
        " print('clean')"
    )
    assert output == "clean"


def test_no_shipped_entry_point_references_the_contract_layer() -> None:
    """The layer is additive: no CLI command, no package import, no runnable surface."""
    for relative in ("memory_tool/cli.py", "memory_tool/__init__.py", "memory_tool/__main__.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "service_contract" not in text, relative


def test_the_contract_layer_has_no_cli_network_or_database_imports() -> None:
    """A contract layer that opened sockets or parsed argv would not be zero-risk."""
    package = ROOT / "memory_tool" / "service_contract"
    forbidden = (
        "import argparse",
        "import socket",
        "import http",
        "import urllib",
        "import sqlite3",
    )
    for path in sorted(package.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path.name} imports {needle}"
