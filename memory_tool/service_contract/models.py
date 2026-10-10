"""Transport-agnostic request, result and projection models for the service contract.

Nothing here touches a database, a socket or the CLI: these are the shapes the application
layer exchanges, plus the pure functions that define their semantics (payload hashing, the
default claim status, the project registry).  The in-process adapter exercises them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import WireSchemaViolation
from .identity import (
    EffectiveScope,
    Face,
    RequestedScope,
    Visibility,
    reject_server_owned_fields,
)
from .versioning import CONTRACT_VERSION, check_contract_version, validate_wire

#: A memory whose project could not be resolved.  Guessing from a directory name is forbidden.
UNASSIGNED = "unassigned"


class ClaimStatus(str, Enum):
    """How strongly a claim is asserted.  ``UNDECLARED`` is the default and stays the default."""

    PROPOSED = "proposed"
    ASSERTED = "asserted"
    UNVERIFIED = "unverified"
    UNDECLARED = "undeclared"


class Lifecycle(str, Enum):
    """Where a memory unit sits in the §4 state machine."""

    PROPOSED = "proposed"
    ASSERTED = "asserted"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    RETRACTED = "retracted"
    ERASED = "erased"


def parse_claim_status(value: Any) -> ClaimStatus:
    """Parse a claim status, treating ``None``/absent as ``undeclared``.

    Args:
        value: Raw value from a payload.

    Returns:
        The parsed status.

    Raises:
        WireSchemaViolation: If the value is not one of the four declared statuses.
    """
    if value is None:
        return ClaimStatus.UNDECLARED
    if isinstance(value, ClaimStatus):
        return value
    try:
        return ClaimStatus(str(value))
    except ValueError as exc:
        raise WireSchemaViolation(
            "unknown claim_status",
            field="claim_status",
            value=str(value),
            allowed=[status.value for status in ClaimStatus],
        ) from exc


def payload_hash(payload: Mapping[str, Any]) -> str:
    """Hash a payload canonically so replays can be compared without storing the payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceLink:
    """A span of a source revision that supports a claim.  Approval without one is refused."""

    source_id: str
    revision: str
    span: str

    def to_wire(self) -> Dict[str, str]:
        """Render the evidence link for the wire."""
        return {"source_id": self.source_id, "revision": self.revision, "span": self.span}

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "EvidenceLink":
        """Parse an evidence link from a wire payload."""
        return cls(
            source_id=str(payload["source_id"]),
            revision=str(payload["revision"]),
            span=str(payload["span"]),
        )


@dataclass(frozen=True)
class EventSubmission:
    """One idempotent client event (design §2.2).

    The server adds ``owner_id``/``space_id``/``device_id``/``actor_agent_id`` from the
    credential; they are not fields of this object at all, which is why a client cannot send
    them.
    """

    client_event_id: str
    content: str
    source_instance: str = ""
    thread_id: str = ""
    source_seq: int = 0
    role: str = "user"
    observed_at: str = ""
    source_app: str = "unknown"
    project_id: Optional[str] = None
    requested_scope: RequestedScope = field(default_factory=RequestedScope)
    contract_version: str = CONTRACT_VERSION
    memory: Optional["MemoryProposal"] = None

    def to_wire(self) -> Dict[str, Any]:
        """Render the client-shaped request payload (no server-owned field)."""
        payload: Dict[str, Any] = {
            "contract_version": self.contract_version,
            "client_event_id": self.client_event_id,
            "source_instance": self.source_instance,
            "source": {"source_app": self.source_app, "thread_id": self.thread_id},
            "source_seq": self.source_seq,
            "role": self.role,
            "observed_at": self.observed_at,
            "content": self.content,
            "requested_scope": self.requested_scope.to_wire(),
        }
        if self.project_id is not None:
            payload["project_id"] = self.project_id
        if self.memory is not None:
            payload["memory"] = self.memory.to_wire()
        return payload

    def payload_hash(self) -> str:
        """Hash the client-shaped payload: this is what idempotency compares."""
        return payload_hash(self.to_wire())

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "EventSubmission":
        """Parse and validate a client event payload.

        Order matters: server-owned fields are refused *before* schema validation, so a client
        that tries to declare ``owner_id`` gets an identity refusal rather than a generic
        "unexpected field".

        Args:
            payload: The client-supplied payload.

        Returns:
            The parsed submission.

        Raises:
            IdentityFieldRejected: If a server-owned field is present.
            WireSchemaViolation: If the payload does not match the versioned schema.
        """
        reject_server_owned_fields(payload)
        validate_wire(payload, "event-submission")
        check_contract_version(payload.get("contract_version"))
        source = payload.get("source") or {}
        nested = payload.get("memory")
        return cls(
            client_event_id=str(payload["client_event_id"]),
            content=str(payload["content"]),
            source_instance=str(payload.get("source_instance", "")),
            thread_id=str(source.get("thread_id", "")),
            source_seq=int(payload["source_seq"]),
            role=str(payload["role"]),
            observed_at=str(payload["observed_at"]),
            source_app=str(source["source_app"]),
            project_id=payload.get("project_id"),
            requested_scope=RequestedScope.from_wire(payload.get("requested_scope")),
            contract_version=str(payload["contract_version"]),
            memory=MemoryProposal.from_wire(nested) if isinstance(nested, Mapping) else None,
        )


@dataclass(frozen=True)
class MemoryProposal:
    """One memory revision proposal.  ``claim_status`` defaults to undeclared, always."""

    kind: str
    content: str
    claim_status: ClaimStatus = ClaimStatus.UNDECLARED
    memory_id: Optional[str] = None
    base_revision: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    evidence: Tuple[EvidenceLink, ...] = ()
    project_id: Optional[str] = None
    requested_scope: RequestedScope = field(default_factory=RequestedScope)
    contract_version: str = CONTRACT_VERSION

    def to_wire(self) -> Dict[str, Any]:
        """Render the client-shaped proposal payload."""
        payload: Dict[str, Any] = {
            "contract_version": self.contract_version,
            "kind": self.kind,
            "content": self.content,
            "claim_status": self.claim_status.value,
            "evidence": [link.to_wire() for link in self.evidence],
            "requested_scope": self.requested_scope.to_wire(),
        }
        for key in ("memory_id", "base_revision", "valid_from", "valid_to", "project_id"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload

    def payload_hash(self) -> str:
        """Hash the client-shaped payload."""
        return payload_hash(self.to_wire())

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "MemoryProposal":
        """Parse and validate a memory proposal payload.

        Args:
            payload: The client-supplied payload.

        Returns:
            The parsed proposal, with ``claim_status`` read as ``undeclared`` when absent or null.

        Raises:
            IdentityFieldRejected: If a server-owned field is present.
            WireSchemaViolation: If the payload does not match the versioned schema.
        """
        reject_server_owned_fields(payload)
        validate_wire(payload, "memory-proposal")
        if "contract_version" in payload:
            check_contract_version(payload.get("contract_version"))
        evidence = tuple(
            EvidenceLink.from_wire(item) for item in payload.get("evidence", ()) or ()
        )
        return cls(
            kind=str(payload["kind"]),
            content=str(payload["content"]),
            claim_status=parse_claim_status(payload.get("claim_status")),
            memory_id=payload.get("memory_id"),
            base_revision=payload.get("base_revision"),
            valid_from=payload.get("valid_from"),
            valid_to=payload.get("valid_to"),
            evidence=evidence,
            project_id=payload.get("project_id"),
            requested_scope=RequestedScope.from_wire(payload.get("requested_scope")),
            contract_version=str(payload.get("contract_version", CONTRACT_VERSION)),
        )


@dataclass(frozen=True)
class FaceRequest:
    """A read request against one face.  It expresses desired scope, never identity."""

    query: str = ""
    memory_id: Optional[str] = None
    revision_id: Optional[str] = None
    requested_scope: RequestedScope = field(default_factory=RequestedScope)
    limit: int = 20
    offline: bool = False

    def matches(self, text: str) -> bool:
        """Return whether ``text`` satisfies this request's query (literal, case-insensitive)."""
        if not self.query.strip():
            return True
        return self.query.lower() in text.lower()


@dataclass(frozen=True)
class Degradation:
    """A named degradation.  Degrading is allowed; degrading silently is not."""

    name: str
    detail: str = ""

    def to_wire(self) -> Dict[str, str]:
        """Render the degradation for the wire."""
        return {"name": self.name, "detail": self.detail}


@dataclass(frozen=True)
class FaceItem:
    """One served revision.  ``claim_status`` is reported as stored, never normalised upward."""

    memory_id: str
    revision_id: str
    kind: str
    content: str
    claim_status: ClaimStatus
    lifecycle: Lifecycle
    unit_lifecycle: Lifecycle
    project_id: str
    space_id: str
    visibility: Visibility
    actor_agent_id: str

    def to_wire(self) -> Dict[str, str]:
        """Render the item for the wire."""
        return {
            "memory_id": self.memory_id,
            "revision_id": self.revision_id,
            "kind": self.kind,
            "content": self.content,
            "claim_status": self.claim_status.value,
            "lifecycle": self.lifecycle.value,
            "unit_lifecycle": self.unit_lifecycle.value,
            "project_id": self.project_id,
            "space_id": self.space_id,
            "visibility": self.visibility.value,
            "actor_agent_id": self.actor_agent_id,
        }


@dataclass(frozen=True)
class FaceResult:
    """The result of one face read, with the scope it was actually served under."""

    face: Face
    items: List[FaceItem] = field(default_factory=list)
    effective_scope: Optional[EffectiveScope] = None
    denied: Tuple[str, ...] = ()
    stale: bool = False
    served_from_cache: bool = False
    degradations: Tuple[Degradation, ...] = ()

    def to_wire(self) -> Dict[str, Any]:
        """Render the result for the wire."""
        return {
            "face": self.face.value,
            "items": [item.to_wire() for item in self.items],
            "effective_scope": self.effective_scope.to_wire() if self.effective_scope else None,
            "denied": list(self.denied),
            "stale": self.stale,
            "served_from_cache": self.served_from_cache,
            "degradations": [item.to_wire() for item in self.degradations],
        }


@dataclass(frozen=True)
class EventView:
    """A scope-checked read of a stored source event (canonical log, not a search projection)."""

    client_event_id: str
    server_seq: int
    content: str
    source_instance: str
    source_seq: int
    observed_at: str
    received_at: str
    source_app: str
    owner_id: str
    space_id: str
    project_id: str
    device_id: str
    actor_agent_id: str
    payload_hash: str

    def to_wire(self) -> Dict[str, Any]:
        """Render the event view for the wire."""
        return {
            "client_event_id": self.client_event_id,
            "server_seq": self.server_seq,
            "content": self.content,
            "source_instance": self.source_instance,
            "source_seq": self.source_seq,
            "observed_at": self.observed_at,
            "received_at": self.received_at,
            "source_app": self.source_app,
            "owner_id": self.owner_id,
            "space_id": self.space_id,
            "project_id": self.project_id,
            "device_id": self.device_id,
            "actor_agent_id": self.actor_agent_id,
            "payload_hash": self.payload_hash,
        }


@dataclass(frozen=True)
class Tombstone:
    """A retraction/erasure marker.  It deliberately carries no original text."""

    memory_id: str
    owner_id: str
    space_id: str
    project_id: str
    kind: str
    reason: str
    created_at: str
    content_absent: bool = True
    receipt_id: Optional[str] = None

    def to_wire(self) -> Dict[str, Any]:
        """Render the tombstone for the wire."""
        return {
            "memory_id": self.memory_id,
            "owner_id": self.owner_id,
            "space_id": self.space_id,
            "project_id": self.project_id,
            "kind": self.kind,
            "reason": self.reason,
            "created_at": self.created_at,
            "content_absent": self.content_absent,
            "receipt_id": self.receipt_id,
        }


#: Derived objects an erasure plan must enumerate.  Anything not listed here is not covered.
ERASURE_TARGETS: Tuple[str, ...] = (
    "revision_body",
    "source_span",
    "vector",
    "graph_edges",
    "summary",
    "client_cache",
    "attachment",
)


@dataclass(frozen=True)
class ErasureManifest:
    """A replayable erasure plan.  Replay is idempotent and never resurrections a memory."""

    manifest_id: str
    memory_id: str
    targets: Tuple[str, ...]
    store_side_targets: Tuple[str, ...]
    pending_targets: Tuple[str, ...]
    deferred_targets: Tuple[str, ...]
    created_at: str
    complete: bool

    def to_wire(self) -> Dict[str, Any]:
        """Render the manifest for the wire."""
        return {
            "manifest_id": self.manifest_id,
            "memory_id": self.memory_id,
            "targets": list(self.targets),
            "store_side_targets": list(self.store_side_targets),
            "pending_targets": list(self.pending_targets),
            "deferred_targets": list(self.deferred_targets),
            "created_at": self.created_at,
            "complete": self.complete,
        }

    def with_pending(self, pending: Sequence[str], deferred: Sequence[str]) -> "ErasureManifest":
        """Return a copy with updated progress."""
        return replace(
            self,
            pending_targets=tuple(pending),
            deferred_targets=tuple(deferred),
            complete=not pending and not deferred,
        )


@dataclass(frozen=True)
class ErasureReplay:
    """The outcome of replaying an erasure manifest after a restore."""

    status: str
    manifest_id: str
    remaining: Tuple[str, ...] = ()

    def to_wire(self) -> Dict[str, Any]:
        """Render the replay outcome for the wire."""
        return {
            "status": self.status,
            "manifest_id": self.manifest_id,
            "remaining": list(self.remaining),
        }


@dataclass(frozen=True)
class ChangelogDelta:
    """One changed memory in the changelog, reported once per unit at its latest state."""

    server_seq: int
    memory_id: str
    revision_id: str
    kind: str
    lifecycle: Lifecycle
    operation: str

    def to_wire(self) -> Dict[str, Any]:
        """Render the delta for the wire."""
        return {
            "server_seq": self.server_seq,
            "memory_id": self.memory_id,
            "revision_id": self.revision_id,
            "kind": self.kind,
            "lifecycle": self.lifecycle.value,
            "operation": self.operation,
        }


@dataclass(frozen=True)
class CursorReadResult:
    """A changelog read.  ``resnapshot_required`` never carries a partial delta list."""

    status: str
    cursor: str
    deltas: Tuple[ChangelogDelta, ...] = ()
    epoch: int = 0

    def to_wire(self) -> Dict[str, Any]:
        """Render the cursor read for the wire."""
        return {
            "status": self.status,
            "cursor": self.cursor,
            "deltas": [delta.to_wire() for delta in self.deltas],
            "epoch": self.epoch,
        }


@dataclass(frozen=True)
class ConflictRecord:
    """A recorded revision conflict.  Both proposals stay readable; nothing is merged."""

    conflict_id: str
    memory_id: str
    base_revision: Optional[str]
    revision_ids: Tuple[str, ...]
    principals: Tuple[str, ...]
    created_at: str

    def to_wire(self) -> Dict[str, Any]:
        """Render the conflict record for the wire."""
        return {
            "conflict_id": self.conflict_id,
            "memory_id": self.memory_id,
            "base_revision": self.base_revision,
            "revision_ids": list(self.revision_ids),
            "principals": list(self.principals),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AliasConflict:
    """Two or more projects claim the same alias value; resolution must refuse to guess."""

    alias_kind: str
    alias_value: str
    project_ids: Tuple[str, ...]
    environment: Optional[str] = None

    def to_wire(self) -> Dict[str, Any]:
        """Render the conflict for the wire."""
        return {
            "alias_kind": self.alias_kind,
            "alias_value": self.alias_value,
            "environment": self.environment,
            "project_ids": list(self.project_ids),
        }


class ProjectRegistry:
    """Maps device paths / git remotes / worktrees to stable project ids.

    Rules from the architecture: several device paths and remote aliases may map to one project;
    a fork is not automatically the same project; when an alias is claimed twice the mapping is
    reported as ``unassigned`` for review instead of being guessed.
    """

    def __init__(self) -> None:
        """Create an empty registry; unresolved aliases stay ``unassigned``."""
        self._projects: Dict[str, Tuple[str, str]] = {}
        self._aliases: Dict[Tuple[str, str, Optional[str]], List[str]] = {}

    def register_project(self, project_id: str, owner_id: str, space_id: str) -> None:
        """Register a project id with its owner/space."""
        self._projects[project_id] = (owner_id, space_id)

    def projects(self) -> Dict[str, Tuple[str, str]]:
        """Return the registered projects."""
        return dict(self._projects)

    def add_alias(
        self,
        project_id: str,
        alias_kind: str,
        alias_value: str,
        environment: Optional[str] = None,
    ) -> None:
        """Register an alias for a project.

        A second project claiming the same value is recorded as a conflict rather than
        overwriting the first mapping.
        """
        key = (alias_kind, alias_value, environment)
        owners = self._aliases.setdefault(key, [])
        if project_id not in owners:
            owners.append(project_id)

    def resolve(
        self, alias_kind: str, alias_value: str, environment: Optional[str] = None
    ) -> str:
        """Resolve an alias to a project id, or ``unassigned``.

        Resolution refuses when the alias is unknown **or** when more than one project claims it.
        """
        owners = self._aliases.get((alias_kind, alias_value, environment), [])
        if len(owners) != 1:
            return UNASSIGNED
        return owners[0]

    def resolve_path(self, path: str, environment: Optional[str] = None) -> str:
        """Resolve a device path.  Never falls back to the directory basename."""
        return self.resolve("device_path", path, environment)

    def conflicts(self) -> Tuple[AliasConflict, ...]:
        """Return every alias claimed by more than one project."""
        found = [
            AliasConflict(
                alias_kind=kind,
                alias_value=value,
                environment=environment,
                project_ids=tuple(sorted(owners)),
            )
            for (kind, value, environment), owners in self._aliases.items()
            if len(owners) > 1
        ]
        return tuple(sorted(found, key=lambda item: (item.alias_kind, item.alias_value)))

    def to_wire(self) -> Dict[str, Any]:
        """Render the registry (including conflicts) for review."""
        return {
            "projects": {key: list(value) for key, value in self._projects.items()},
            "aliases": [
                {
                    "alias_kind": kind,
                    "alias_value": value,
                    "environment": environment,
                    "project_ids": list(owners),
                }
                for (kind, value, environment), owners in sorted(
                    self._aliases.items(), key=lambda item: (item[0][0], item[0][1])
                )
            ],
            "conflicts": [conflict.to_wire() for conflict in self.conflicts()],
        }


def visible_to(
    *,
    owner_id: str,
    space_id: str,
    project_id: str,
    visibility: Visibility,
    actor_agent_id: str,
    reader_owner_id: str,
    reader_actor_agent_id: str,
    scope: EffectiveScope,
) -> bool:
    """Return whether a unit is inside a reader's scope and visibility.

    ``agent_private`` is the conservative default: another actor of the same owner does not see
    it.  ``owner_shared`` is the only case that crosses a project boundary, and only inside the
    owner's granted scope.
    """
    if owner_id != reader_owner_id:
        return False
    if visibility is Visibility.AGENT_PRIVATE:
        return scope.covers(space_id, project_id) and actor_agent_id == reader_actor_agent_id
    if visibility is Visibility.OWNER_SHARED:
        return space_id in scope.spaces or project_id in scope.projects
    return scope.covers(space_id, project_id)


__all__ = [
    "ERASURE_TARGETS",
    "UNASSIGNED",
    "AliasConflict",
    "ChangelogDelta",
    "ClaimStatus",
    "ConflictRecord",
    "CursorReadResult",
    "Degradation",
    "ErasureManifest",
    "ErasureReplay",
    "EventSubmission",
    "EventView",
    "EvidenceLink",
    "FaceItem",
    "FaceRequest",
    "FaceResult",
    "Lifecycle",
    "MemoryProposal",
    "ProjectRegistry",
    "Tombstone",
    "parse_claim_status",
    "payload_hash",
    "visible_to",
]
