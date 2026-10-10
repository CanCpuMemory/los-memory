"""The isolated in-memory store behind the in-process adapter.

This is deliberately **not** ``service.sqlite3``: no file is created, no migration runs, and the
module is not imported by the CLI.  It exists so the versioned contract has an executable
reference whose refusals can be tested today (design §7.5: "P2-01 only delivers the contract plus
one in-process adapter").  P2-02 replaces the store with SQLite and keeps the contract.

The canonical event log is the source of truth for idempotency: receipts are *derived* from it.
That is what makes restoring a backup safe - replaying the same ``client_event_id`` still finds
exactly one committed event, without trusting a receipt table that the restore may have rolled
back.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .identity import Action, CredentialRegistry, Grant, Principal, Visibility
from .models import (
    ClaimStatus,
    ConflictRecord,
    ErasureManifest,
    EvidenceLink,
    Lifecycle,
    Tombstone,
)

Counters = Dict[str, int]


def _as_claim_status(value: Any) -> ClaimStatus:
    """Coerce a stored value into a claim status."""
    return value if isinstance(value, ClaimStatus) else ClaimStatus(str(value))


def _as_lifecycle(value: Any) -> Lifecycle:
    """Coerce a stored value into a lifecycle state."""
    return value if isinstance(value, Lifecycle) else Lifecycle(str(value))


def _as_visibility(value: Any) -> Visibility:
    """Coerce a stored value into a visibility."""
    return value if isinstance(value, Visibility) else Visibility(str(value))


@dataclass(frozen=True)
class StoredEvent:
    """One committed source event.  The ``(principal, client_event_id)`` pair is unique here."""

    server_seq: int
    principal_key_id: str
    owner_id: str
    space_id: str
    project_id: str
    client_event_id: str
    source_instance: str
    thread_id: str
    source_seq: int
    role: str
    observed_at: str
    received_at: str
    content: str
    source_app: str
    device_id: str
    actor_agent_id: str
    payload_hash: str


@dataclass(frozen=True)
class MemoryUnit:
    """A memory and its current revision.  Scope and lifecycle live here, not on the revision."""

    memory_id: str
    owner_id: str
    space_id: str
    project_id: str
    kind: str
    visibility: Visibility
    actor_agent_id: str
    current_revision: str
    lifecycle: Lifecycle
    created_at: str
    server_seq: int
    imported_read_only: bool = False


@dataclass(frozen=True)
class Revision:
    """An immutable revision.  Erasure marks it unreachable; the row itself is not rewritten."""

    revision_id: str
    memory_id: str
    parent_revision: Optional[str]
    content: str
    claim_status: ClaimStatus
    kind: str
    valid_from: Optional[str]
    valid_to: Optional[str]
    recorded_at: str
    extractor_version: str
    evidence: Tuple[EvidenceLink, ...]
    state: Lifecycle
    operation: str
    server_seq: int
    created_by: str = ""
    erased: bool = False
    conflicted: bool = False


@dataclass(frozen=True)
class OutboxEntry:
    """A pending projection job, written in the same transaction as the event and the receipt."""

    job_id: str
    operation: str
    idempotency_key: str
    target: str
    created_at: str
    attempts: int = 0
    budget: int = 5


@dataclass(frozen=True)
class CursorRecord:
    """An opaque cursor's server-side binding: ``(owner, space, permission_epoch)``."""

    cursor: str
    principal_key_id: str
    owner_id: str
    space_id: str
    epoch: int
    after_seq: int
    created_at: str


@dataclass(frozen=True)
class CacheEntry:
    """A previously served revision, kept so the cache face can be probed for staleness."""

    memory_id: str
    revision_id: str
    served_at: float


@dataclass
class InMemoryStore:
    """Every piece of state the contract layer owns, with a JSON-safe snapshot round trip."""

    principals: CredentialRegistry = field(default_factory=CredentialRegistry)
    grants: List[Grant] = field(default_factory=list)
    events: List[StoredEvent] = field(default_factory=list)
    receipts: List[Any] = field(default_factory=list)
    outbox: List[OutboxEntry] = field(default_factory=list)
    units: Dict[str, MemoryUnit] = field(default_factory=dict)
    revisions: Dict[str, Revision] = field(default_factory=dict)
    revision_order: Dict[str, List[str]] = field(default_factory=dict)
    tombstones: Dict[str, Tombstone] = field(default_factory=dict)
    erasures: Dict[str, ErasureManifest] = field(default_factory=dict)
    conflicts: List[ConflictRecord] = field(default_factory=list)
    visibility_policies: Dict[Tuple[str, str, str, str], Visibility] = field(default_factory=dict)
    projection_deadlines: Dict[str, float] = field(default_factory=dict)
    cursors: Dict[str, CursorRecord] = field(default_factory=dict)
    cache: Dict[str, List[CacheEntry]] = field(default_factory=dict)
    epochs: Dict[Tuple[str, str], int] = field(default_factory=dict)
    counters: Counters = field(
        default_factory=lambda: {
            "server_seq": 0,
            "revision": 0,
            "memory": 0,
            "receipt": 0,
            "manifest": 0,
            "conflict": 0,
        }
    )

    # ---------------------------------------------------------------- sequences and epochs

    def next_seq(self, name: str) -> int:
        """Return the next value of a monotonic counter."""
        self.counters[name] += 1
        return self.counters[name]

    def current_seq(self) -> int:
        """Return the newest assigned ``server_seq``."""
        return self.counters["server_seq"]

    def bump_epoch(self, owner_id: str, space_id: str) -> int:
        """Increment the permission epoch of an owner/space pair."""
        key = (owner_id, space_id)
        self.epochs[key] = self.epochs.get(key, 0) + 1
        return self.epochs[key]

    def epoch(self, owner_id: str, space_id: str) -> int:
        """Return the permission epoch of an owner/space pair."""
        return self.epochs.get((owner_id, space_id), 0)

    # --------------------------------------------------------------------------- lookups

    def find_event(self, principal_key_id: str, client_event_id: str) -> Optional[StoredEvent]:
        """Find the committed event for an idempotency key, if any."""
        for event in self.events:
            if (
                event.principal_key_id == principal_key_id
                and event.client_event_id == client_event_id
            ):
                return event
        return None

    def revisions_of(self, memory_id: str) -> List[Revision]:
        """Return every revision of a memory, oldest first."""
        return [self.revisions[item] for item in self.revision_order.get(memory_id, [])]

    def effective_visibility(self, unit: MemoryUnit) -> Visibility:
        """Return the visibility in force for a unit, honouring a later policy change."""
        key = (unit.owner_id, unit.space_id, unit.project_id, unit.actor_agent_id)
        return self.visibility_policies.get(key, unit.visibility)

    def stats(self) -> Dict[str, int]:
        """Return countable state only - never anything derived from a secret."""
        return {
            "events": len(self.events),
            "receipts": len(self.receipts),
            "outbox": len(self.outbox),
            "memory_units": len(self.units),
            "revisions": len(self.revisions),
            "tombstones": len(self.tombstones),
            "erasure_manifests": len(self.erasures),
            "conflicts": len(self.conflicts),
            "cursors": len(self.cursors),
        }

    # -------------------------------------------------------------------------- snapshots

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-safe snapshot of durable state.

        Credentials appear as hashes only.  The receipt ledger is intentionally left out: it is
        derived, and re-deriving it is what keeps idempotency correct across a restore.
        """
        return {
            "principals": [_dataclass_json(item) for item in self.principals.snapshot()],
            "grants": [_dataclass_json(item) for item in self.grants],
            "events": [_dataclass_json(item) for item in self.events],
            "outbox": [_dataclass_json(item) for item in self.outbox],
            "units": {key: _dataclass_json(value) for key, value in self.units.items()},
            "revisions": {key: _dataclass_json(value) for key, value in self.revisions.items()},
            "revision_order": {key: list(value) for key, value in self.revision_order.items()},
            "tombstones": {key: _dataclass_json(value) for key, value in self.tombstones.items()},
            "erasures": {key: _dataclass_json(value) for key, value in self.erasures.items()},
            "conflicts": [_dataclass_json(item) for item in self.conflicts],
            "visibility_policies": {
                "|".join(key): value.value for key, value in self.visibility_policies.items()
            },
            "projection_deadlines": dict(self.projection_deadlines),
            "cursors": {key: _dataclass_json(value) for key, value in self.cursors.items()},
            "epochs": {"|".join(key): value for key, value in self.epochs.items()},
            "counters": dict(self.counters),
        }

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        """Replace durable state from a snapshot.

        Args:
            snapshot: A value previously returned by :meth:`snapshot`.
        """
        self.principals.restore([_principal(item) for item in snapshot["principals"]])
        self.grants = [_grant(item) for item in snapshot["grants"]]
        self.events = [_event(item) for item in snapshot["events"]]
        self.outbox = [_outbox(item) for item in snapshot["outbox"]]
        self.units = {key: _unit(value) for key, value in snapshot["units"].items()}
        self.revisions = {key: _revision(value) for key, value in snapshot["revisions"].items()}
        self.revision_order = {
            key: list(value) for key, value in snapshot["revision_order"].items()
        }
        self.tombstones = {
            key: _tombstone(value) for key, value in snapshot["tombstones"].items()
        }
        self.erasures = {key: _erasure(value) for key, value in snapshot["erasures"].items()}
        self.conflicts = [_conflict(item) for item in snapshot["conflicts"]]
        self.visibility_policies = {}
        for key, value in snapshot["visibility_policies"].items():
            owner, space, project, actor = key.split("|")
            self.visibility_policies[(owner, space, project, actor)] = _as_visibility(value)
        self.projection_deadlines = dict(snapshot["projection_deadlines"])
        self.cursors = {key: _cursor(value) for key, value in snapshot["cursors"].items()}
        self.epochs = {}
        for key, value in snapshot["epochs"].items():
            owner, space = key.split("|")
            self.epochs[(owner, space)] = int(value)
        self.counters = {key: int(value) for key, value in snapshot["counters"].items()}
        self.cache = {}
        self.receipts = []


def _dataclass_json(value: Any) -> Any:
    """Convert a frozen dataclass into a JSON-safe dict."""
    raw = dataclasses.asdict(value)
    return _normalise(raw)


def _normalise(value: Any) -> Any:
    """Replace enum values and tuples with JSON-safe equivalents."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (tuple, list)):
        return [_normalise(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in value.items()}
    return value


def _principal(payload: Mapping[str, Any]) -> Principal:
    return Principal(**dict(payload))


def _grant(payload: Mapping[str, Any]) -> Grant:
    data = dict(payload)
    data["action"] = Action(str(data["action"]))
    return Grant(**data)  # type: ignore[arg-type]


def _event(payload: Mapping[str, Any]) -> StoredEvent:
    return StoredEvent(**dict(payload))


def _outbox(payload: Mapping[str, Any]) -> OutboxEntry:
    return OutboxEntry(**dict(payload))


def _unit(payload: Mapping[str, Any]) -> MemoryUnit:
    data = dict(payload)
    data["visibility"] = _as_visibility(data["visibility"])
    data["lifecycle"] = _as_lifecycle(data["lifecycle"])
    return MemoryUnit(**data)  # type: ignore[arg-type]


def _revision(payload: Mapping[str, Any]) -> Revision:
    data = dict(payload)
    data["claim_status"] = _as_claim_status(data["claim_status"])
    data["state"] = _as_lifecycle(data["state"])
    data["evidence"] = tuple(
        link if isinstance(link, EvidenceLink) else EvidenceLink(**dict(link))
        for link in data.get("evidence", ())
    )
    return Revision(**data)  # type: ignore[arg-type]


def _tombstone(payload: Mapping[str, Any]) -> Tombstone:
    return Tombstone(**dict(payload))


def _with_tuples(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Decode JSON lists back into the tuples these records declare."""
    return {
        key: (tuple(value) if isinstance(value, list) else value)
        for key, value in payload.items()
    }


def _erasure(payload: Mapping[str, Any]) -> ErasureManifest:
    return ErasureManifest(**_with_tuples(payload))  # type: ignore[arg-type]


def _conflict(payload: Mapping[str, Any]) -> ConflictRecord:
    return ConflictRecord(**_with_tuples(payload))  # type: ignore[arg-type]


def _cursor(payload: Mapping[str, Any]) -> CursorRecord:
    data = dict(payload)
    data["epoch"] = int(data["epoch"])
    data["after_seq"] = int(data["after_seq"])
    return CursorRecord(**data)  # type: ignore[arg-type]


__all__ = [
    "CacheEntry",
    "CursorRecord",
    "InMemoryStore",
    "MemoryUnit",
    "OutboxEntry",
    "Revision",
    "StoredEvent",
]
