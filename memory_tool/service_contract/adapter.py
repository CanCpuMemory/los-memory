"""The in-process adapter: one loop, transport-independent enforcement of the contract.

Everything a transport (stdio MCP today, HTTPS later) would do is expressed here as plain
method calls with typed results, so the contract can be exercised without a server, a socket or
a database.  The adapter is *not* imported by the CLI and creates no file.
"""
from __future__ import annotations

import copy
import secrets
import time
from dataclasses import replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import (
    ErasedRecordNotReadable,
    EvidenceRequiredForAssertion,
    FaceNotGranted,
    IdempotencyConflict,
    RecordNotFound,
    RetractedRecordNotReadable,
    RevisionConflict,
    ScopeNotGranted,
)
from .identity import (
    FACE_ACTION,
    Action,
    Credential,
    EffectiveScope,
    Face,
    Grant,
    Principal,
    Registration,
    RequestedScope,
    Visibility,
    compute_effective_scope,
    utc_now_iso,
)
from .models import (
    ERASURE_TARGETS,
    UNASSIGNED,
    ChangelogDelta,
    ClaimStatus,
    ConflictRecord,
    CursorReadResult,
    Degradation,
    ErasureManifest,
    ErasureReplay,
    EventSubmission,
    EventView,
    FaceItem,
    FaceRequest,
    FaceResult,
    Lifecycle,
    MemoryProposal,
    Tombstone,
    payload_hash,
    visible_to,
)
from .receipts import Receipt, ReceiptState, Stages
from .store import (
    CacheEntry,
    CursorRecord,
    InMemoryStore,
    MemoryUnit,
    OutboxEntry,
    Revision,
    StoredEvent,
)
from .versioning import check_contract_version

#: A retracted memory's derived state must be invalidated within this budget (design §2.4/N8).
PROJECTION_INVALIDATION_SECONDS = 300

RESNAPSHOT_REQUIRED = "resnapshot_required"
CHANGELOG_OK = "ok"

#: No extractor runs in the contract layer; saying so is more honest than inventing a version.
EXTRACTOR_VERSION = "undeclared"


class InProcessAdapter:
    """A single-writer, in-memory reference implementation of the P2-01 contract."""

    def __init__(
        self,
        *,
        fault_injector: Optional[Callable[[str], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Create an isolated adapter.

        Args:
            fault_injector: Optional test seam.  It is called with a stage name at each commit
                boundary so atomicity can be probed by raising from it.
            clock: Optional clock returning epoch seconds.
        """
        self._store = InMemoryStore()
        self._fault_injector = fault_injector
        self._clock = clock or time.time
        self._conflict_counter = 0

    # ------------------------------------------------------------------------ time helpers

    def now(self) -> float:
        """Return the current epoch seconds."""
        return float(self._clock())

    def now_iso(self) -> str:
        """Return the current instant in the repo's UTC format."""
        return utc_now_iso()

    def _fault(self, stage: str) -> None:
        """Run the fault injector, if any."""
        if self._fault_injector is not None:
            self._fault_injector(stage)

    # ----------------------------------------------------------------------------- identity

    def register_device(
        self,
        owner_id: str,
        device_id: str,
        *,
        key_id: Optional[str] = None,
        actor_agent_id: str = "unknown",
    ) -> Registration:
        """Register a device credential and return its one-time secret."""
        return self._store.principals.register(
            owner_id,
            device_id,
            key_id=key_id,
            actor_agent_id=actor_agent_id,
            now=self.now_iso(),
        )

    def revoke_key(self, key_id: str) -> None:
        """Revoke a credential and bump nothing else: the key is the authority."""
        self._store.principals.revoke(key_id, now=self.now_iso())

    def grant(
        self,
        owner_id: str,
        space_id: str,
        project_id: str,
        action: Action,
        *,
        expires_at: Optional[str] = None,
    ) -> Grant:
        """Add a ``(owner, space, project, action)`` grant and bump the permission epoch."""
        entry = Grant(
            owner_id=owner_id,
            space_id=space_id,
            project_id=project_id,
            action=Action(action),
            expires_at=expires_at,
        )
        self._store.grants.append(entry)
        self._store.bump_epoch(owner_id, space_id)
        return entry

    def revoke_grant(
        self, owner_id: str, space_id: str, project_id: str, action: Action
    ) -> int:
        """Revoke matching grants and bump the permission epoch.

        Returns:
            How many active grants were revoked.
        """
        wanted = Action(action)
        revoked = 0
        updated: List[Grant] = []
        for entry in self._store.grants:
            if (
                entry.owner_id == owner_id
                and entry.space_id == space_id
                and entry.project_id == project_id
                and entry.action is wanted
                and entry.revoked_at is None
            ):
                entry = replace(entry, revoked_at=self.now_iso())
                revoked += 1
            updated.append(entry)
        self._store.grants = updated
        self._store.bump_epoch(owner_id, space_id)
        return revoked

    def permission_epoch(self, owner_id: str, space_id: str) -> int:
        """Return the current permission epoch of an owner/space pair."""
        return self._store.epoch(owner_id, space_id)

    def set_visibility_policy(
        self,
        owner_id: str,
        space_id: str,
        project_id: str,
        actor_agent_id: str,
        visibility: Visibility,
    ) -> Visibility:
        """Set the server-side visibility policy for one actor's units.

        Visibility is computed by the server (invariant I4), so this is an authorization change,
        not a client write: it applies to that actor's units and bumps the permission epoch.
        """
        resolved = Visibility(visibility)
        self._store.visibility_policies[
            (owner_id, space_id, project_id, actor_agent_id)
        ] = resolved
        self._store.bump_epoch(owner_id, space_id)
        return resolved

    def _authenticate(self, credential: Credential) -> Principal:
        """Authenticate a credential."""
        return self._store.principals.authenticate(credential)

    def _scope(
        self,
        principal: Principal,
        requested: RequestedScope,
        required_action: Optional[Action],
    ) -> EffectiveScope:
        """Compute the effective scope for a request."""
        return compute_effective_scope(
            principal, self._store.grants, requested, self.now_iso(), required_action
        )

    def effective_scope(self, credential: Credential, request: FaceRequest) -> EffectiveScope:
        """Return the effective scope a read request would be served under."""
        principal = self._authenticate(credential)
        return self._scope(principal, request.requested_scope, None)

    def _space_for(self, principal: Principal, project_id: str) -> str:
        """Return a granted space for a project, or ``unassigned``."""
        now = self.now_iso()
        for entry in self._store.grants:
            if (
                entry.owner_id == principal.owner_id
                and entry.project_id == project_id
                and entry.is_active(now)
            ):
                return entry.space_id
        return UNASSIGNED

    def _attribute(
        self, principal: Principal, scope: EffectiveScope, requested_project: Optional[str]
    ) -> Tuple[str, str]:
        """Decide the server-side ``(space, project)`` of a write.

        Raises:
            ScopeNotGranted: If a project was named outside the effective scope.
        """
        if requested_project:
            if requested_project not in scope.projects:
                raise ScopeNotGranted(
                    "the named project is outside the principal's grants",
                    project_id=requested_project,
                    granted_projects=list(scope.projects),
                )
            return self._space_for(principal, requested_project), requested_project
        if len(scope.projects) == 1 and len(scope.spaces) == 1:
            return scope.spaces[0], scope.projects[0]
        return UNASSIGNED, UNASSIGNED

    # ------------------------------------------------------------------------ atomic commit

    def _transaction(self, body: Callable[[], Any], touched: Sequence[str]) -> Any:
        """Run a write atomically with respect to the store and return its result.

        Sequence numbers are allocated *inside* ``body``, so a failed commit consumes none and a
        retry reuses the same one.  A fault raised after the commit boundary is deliberately not
        rolled back: that is the "durable but the answer was lost" case, which the caller
        resolves by replaying the same idempotency key.
        """
        saved = {name: copy.deepcopy(getattr(self._store, name)) for name in touched}
        saved_counters = dict(self._store.counters)
        self._fault("before_commit")
        try:
            result = body()
        except BaseException:
            for name, value in saved.items():
                setattr(self._store, name, value)
            self._store.counters = saved_counters
            raise
        self._fault("after_commit")
        return result

    # ----------------------------------------------------------------------------- receipts

    def _receipt_from_event(
        self,
        event: StoredEvent,
        principal: Principal,
        scope: Optional[EffectiveScope],
        *,
        replayed: bool,
    ) -> Receipt:
        """Derive the receipt for a committed event.

        Derivation (rather than a lookup in a receipt table) is what makes a restore safe: the
        event log alone reproduces the same ``receipt_id`` and ``server_seq``.
        """
        return Receipt(
            receipt_id=f"receipt-{event.server_seq}",
            state=ReceiptState.ACCEPTED,
            operation="event.append",
            principal_key_id=event.principal_key_id,
            owner_id=event.owner_id,
            client_event_id=event.client_event_id,
            payload_hash=event.payload_hash,
            received_at=event.received_at,
            server_seq=event.server_seq,
            observed_at=event.observed_at,
            source_app=event.source_app,
            replayed=replayed,
            stages=Stages(canonical_committed=True),
            degradations=(
                Degradation(
                    "projections_pending",
                    "the event is durably stored; lexical and semantic projections are not built",
                ),
            ),
            effective_scope=scope,
            denied=scope.denied if scope else (),
        )

    def receipt_for(self, credential: Credential, client_event_id: str) -> Optional[Receipt]:
        """Return the receipt a client would get by replaying ``client_event_id``."""
        principal = self._authenticate(credential)
        event = self._store.find_event(principal.key_id, client_event_id)
        if event is None:
            return None
        scope = self._scope(principal, RequestedScope(), None)
        return self._receipt_from_event(event, principal, scope, replayed=True)

    # -------------------------------------------------------------------------- write path

    def append_event(self, credential: Credential, submission: EventSubmission) -> Receipt:
        """Append one client event idempotently.

        Repeated submissions of the same payload return the same receipt; a different payload
        under the same idempotency key is refused without touching the stored record.

        Args:
            credential: The caller's credential.
            submission: The event.

        Returns:
            The accepted (or replayed) receipt.

        Raises:
            Unauthenticated: If the credential is not valid.
            IdentityFieldRejected: If the payload declared server-owned fields.
            ScopeNotGranted: If the request named scope outside the grants.
            IdempotencyConflict: If the key exists with a different payload hash.
        """
        principal = self._authenticate(credential)
        check_contract_version(submission.contract_version)
        scope = self._scope(principal, submission.requested_scope, Action.EVENT_APPEND)
        space_id, project_id = self._attribute(principal, scope, submission.project_id)
        digest = submission.payload_hash()

        existing = self._store.find_event(principal.key_id, submission.client_event_id)
        if existing is not None:
            if existing.payload_hash != digest:
                raise IdempotencyConflict(
                    "the idempotency key is already committed with a different payload",
                    stored_receipt=self._receipt_from_event(
                        existing, principal, scope, replayed=False
                    ),
                    client_event_id=submission.client_event_id,
                    stored_payload_hash=existing.payload_hash,
                    submitted_payload_hash=digest,
                    server_seq=existing.server_seq,
                )
            return self._receipt_from_event(existing, principal, scope, replayed=True)

        def body() -> Receipt:
            seq = self._store.next_seq("server_seq")
            event = StoredEvent(
                server_seq=seq,
                principal_key_id=principal.key_id,
                owner_id=principal.owner_id,
                space_id=space_id,
                project_id=project_id,
                client_event_id=submission.client_event_id,
                source_instance=submission.source_instance,
                thread_id=submission.thread_id,
                source_seq=submission.source_seq,
                role=submission.role,
                observed_at=submission.observed_at,
                received_at=self.now_iso(),
                content=submission.content,
                source_app=submission.source_app,
                device_id=principal.device_id,
                actor_agent_id=principal.actor_agent_id,
                payload_hash=digest,
            )
            receipt = self._receipt_from_event(event, principal, scope, replayed=False)
            self._store.events.append(event)
            self._fault("after_event_write")
            self._store.receipts.append(receipt)
            self._store.outbox.append(
                OutboxEntry(
                    job_id=f"job-{seq}",
                    operation="project.event",
                    idempotency_key=f"{principal.key_id}:{submission.client_event_id}",
                    target=f"event:{seq}",
                    created_at=event.received_at,
                )
            )
            return receipt

        return self._transaction(body, touched=("events", "receipts", "outbox"))

    def get_event(self, credential: Credential, client_event_id: str) -> EventView:
        """Read a committed event by its idempotency key, inside the caller's scope.

        This reads the canonical log directly; it does not imply the event is in any search
        projection (see the receipt's stage flags).
        """
        principal = self._authenticate(credential)
        scope = self._scope(principal, RequestedScope(), Action.READ)
        event = self._store.find_event(principal.key_id, client_event_id)
        if event is None:
            raise RecordNotFound("no committed event for that client_event_id")
        if not scope.covers(event.space_id, event.project_id):
            raise ScopeNotGranted(
                "the event is outside the caller's effective scope",
                space_id=event.space_id,
                project_id=event.project_id,
            )
        return EventView(
            client_event_id=event.client_event_id,
            server_seq=event.server_seq,
            content=event.content,
            source_instance=event.source_instance,
            source_seq=event.source_seq,
            observed_at=event.observed_at,
            received_at=event.received_at,
            source_app=event.source_app,
            owner_id=event.owner_id,
            space_id=event.space_id,
            project_id=event.project_id,
            device_id=event.device_id,
            actor_agent_id=event.actor_agent_id,
            payload_hash=event.payload_hash,
        )

    def propose_memory(self, credential: Credential, proposal: MemoryProposal) -> Receipt:
        """Create a memory or a new revision of one, refusing a stale base revision.

        Raises:
            EvidenceRequiredForAssertion: If ``asserted`` was requested without evidence.
            RevisionConflict: If ``base_revision`` is no longer current.  The losing proposal is
                persisted and the conflict recorded; nothing is merged or overwritten.
        """
        principal = self._authenticate(credential)
        check_contract_version(proposal.contract_version)
        required = Action.MEMORY_REVISE if proposal.memory_id else Action.MEMORY_PROPOSE
        scope = self._scope(principal, proposal.requested_scope, required)
        if proposal.claim_status is ClaimStatus.ASSERTED and not proposal.evidence:
            raise EvidenceRequiredForAssertion(
                "a claim cannot be asserted without at least one evidence span",
                claim_status=proposal.claim_status.value,
            )

        if proposal.memory_id:
            unit = self._store.units.get(proposal.memory_id)
            if unit is None:
                raise RecordNotFound("unknown memory_id", memory_id=proposal.memory_id)
            self._require_in_scope(principal, scope, unit.space_id, unit.project_id)
            if proposal.base_revision != unit.current_revision:
                self._record_conflict(principal, unit, proposal, scope)
            space_id, project_id = unit.space_id, unit.project_id
        else:
            space_id, project_id = self._attribute(principal, scope, proposal.project_id)

        def body() -> Receipt:
            seq = self._store.next_seq("server_seq")
            now = self.now_iso()
            if proposal.memory_id:
                current = self._store.units[proposal.memory_id]
                memory_id = current.memory_id
                revision = Revision(
                    revision_id=f"rev-{seq}",
                    memory_id=memory_id,
                    parent_revision=current.current_revision,
                    content=proposal.content,
                    claim_status=proposal.claim_status,
                    kind=proposal.kind,
                    valid_from=proposal.valid_from,
                    valid_to=proposal.valid_to,
                    recorded_at=now,
                    extractor_version=EXTRACTOR_VERSION,
                    evidence=tuple(proposal.evidence),
                    state=Lifecycle.PROPOSED,
                    operation="memory.revise",
                    server_seq=seq,
                    created_by=principal.key_id,
                )
                self._store.units[memory_id] = replace(
                    current,
                    current_revision=revision.revision_id,
                    lifecycle=Lifecycle.PROPOSED,
                    server_seq=seq,
                )
            else:
                memory_id = f"mem-{seq}"
                revision = Revision(
                    revision_id=f"rev-{seq}",
                    memory_id=memory_id,
                    parent_revision=None,
                    content=proposal.content,
                    claim_status=proposal.claim_status,
                    kind=proposal.kind,
                    valid_from=proposal.valid_from,
                    valid_to=proposal.valid_to,
                    recorded_at=now,
                    extractor_version=EXTRACTOR_VERSION,
                    evidence=tuple(proposal.evidence),
                    state=Lifecycle.PROPOSED,
                    operation="memory.propose",
                    server_seq=seq,
                    created_by=principal.key_id,
                )
                self._store.units[memory_id] = MemoryUnit(
                    memory_id=memory_id,
                    owner_id=principal.owner_id,
                    space_id=space_id,
                    project_id=project_id,
                    kind=proposal.kind,
                    visibility=Visibility.AGENT_PRIVATE,
                    actor_agent_id=principal.actor_agent_id,
                    current_revision=revision.revision_id,
                    lifecycle=Lifecycle.PROPOSED,
                    created_at=now,
                    server_seq=seq,
                )
            self._store.revisions[revision.revision_id] = revision
            self._store.revision_order.setdefault(memory_id, []).append(revision.revision_id)
            self._store.outbox.append(self._projection_job(seq, revision.revision_id))
            receipt = Receipt(
                receipt_id=f"receipt-{seq}",
                state=ReceiptState.ACCEPTED,
                operation=revision.operation,
                principal_key_id=principal.key_id,
                owner_id=principal.owner_id,
                client_event_id=f"mem.{revision.revision_id}",
                payload_hash=payload_hash(proposal.to_wire()),
                received_at=now,
                server_seq=seq,
                stages=Stages(canonical_committed=True),
                degradations=(
                    Degradation(
                        "projections_pending", "the revision is stored but not yet indexed"
                    ),
                ),
                effective_scope=scope,
                denied=scope.denied,
                memory_id=memory_id,
                revision_id=revision.revision_id,
                lifecycle=Lifecycle.PROPOSED.value,
            )
            self._store.receipts.append(receipt)
            return receipt

        return self._transaction(
            body, touched=("units", "revisions", "revision_order", "receipts", "outbox")
        )

    def _projection_job(self, seq: int, revision_id: str) -> OutboxEntry:
        """Build the projection job written in the same transaction as a revision."""
        return OutboxEntry(
            job_id=f"job-{seq}",
            operation="project.revision",
            idempotency_key=f"revision:{revision_id}",
            target=revision_id,
            created_at=self.now_iso(),
        )

    def _record_conflict(
        self,
        principal: Principal,
        unit: MemoryUnit,
        proposal: MemoryProposal,
        scope: EffectiveScope,
    ) -> None:
        """Persist a losing proposal and raise the conflict, with nothing merged.

        Raises:
            RevisionConflict: Always.
        """
        self._conflict_counter += 1
        conflict_id = f"conflict-{self._conflict_counter}"
        holder: Dict[str, Any] = {}

        def body() -> None:
            seq = self._store.next_seq("server_seq")
            losing = Revision(
                revision_id=f"rev-{seq}",
                memory_id=unit.memory_id,
                parent_revision=proposal.base_revision,
                content=proposal.content,
                claim_status=proposal.claim_status,
                kind=proposal.kind,
                valid_from=proposal.valid_from,
                valid_to=proposal.valid_to,
                recorded_at=self.now_iso(),
                extractor_version=EXTRACTOR_VERSION,
                evidence=tuple(proposal.evidence),
                state=Lifecycle.PROPOSED,
                operation="memory.revise",
                server_seq=seq,
                created_by=principal.key_id,
                conflicted=True,
            )
            current = self._store.revisions[unit.current_revision]
            conflict = ConflictRecord(
                conflict_id=conflict_id,
                memory_id=unit.memory_id,
                base_revision=proposal.base_revision,
                revision_ids=(unit.current_revision, losing.revision_id),
                principals=(current.created_by, principal.key_id),
                created_at=losing.recorded_at,
            )
            holder["conflict"] = conflict
            holder["losing_id"] = losing.revision_id
            self._store.revisions[losing.revision_id] = losing
            self._store.revision_order.setdefault(unit.memory_id, []).append(losing.revision_id)
            self._store.conflicts.append(conflict)

        self._transaction(body, touched=("revisions", "revision_order", "conflicts"))
        conflict = holder["conflict"]
        raise RevisionConflict(
            "the base revision is no longer current; nothing was merged or overwritten",
            current_revision=unit.current_revision,
            base_revision=proposal.base_revision,
            proposals=[unit.current_revision, holder["losing_id"]],
            conflict=conflict.to_wire(),
        )

    def approve_memory(
        self,
        credential: Credential,
        memory_id: str,
        *,
        base_revision: str,
        evidence: Sequence[Any],
    ) -> Receipt:
        """Move a memory to ``asserted`` by adding an evidence-bearing revision.

        Raises:
            EvidenceRequiredForAssertion: If no evidence span was supplied (no evidence-free
                approval).
            RevisionConflict: If ``base_revision`` is no longer current.
        """
        principal = self._authenticate(credential)
        if not evidence:
            raise EvidenceRequiredForAssertion(
                "approval must carry at least one evidence span", memory_id=memory_id
            )
        scope = self._scope(principal, RequestedScope(), Action.MEMORY_REVISE)
        unit = self._store.units.get(memory_id)
        if unit is None:
            raise RecordNotFound("unknown memory_id", memory_id=memory_id)
        self._require_in_scope(principal, scope, unit.space_id, unit.project_id)
        if base_revision != unit.current_revision:
            raise RevisionConflict(
                "the base revision is no longer current",
                current_revision=unit.current_revision,
                base_revision=base_revision,
                proposals=[unit.current_revision],
            )

        def body() -> Receipt:
            seq = self._store.next_seq("server_seq")
            current = self._store.revisions[self._store.units[memory_id].current_revision]
            approved = replace(
                current,
                revision_id=f"rev-{seq}",
                parent_revision=current.revision_id,
                claim_status=ClaimStatus.ASSERTED,
                evidence=tuple(evidence),
                state=Lifecycle.ASSERTED,
                operation="memory.approve",
                server_seq=seq,
                recorded_at=self.now_iso(),
                created_by=principal.key_id,
            )
            self._store.units[memory_id] = replace(
                self._store.units[memory_id],
                current_revision=approved.revision_id,
                lifecycle=Lifecycle.ASSERTED,
                server_seq=seq,
            )
            self._store.revisions[approved.revision_id] = approved
            self._store.revision_order.setdefault(memory_id, []).append(approved.revision_id)
            self._store.outbox.append(self._projection_job(seq, approved.revision_id))
            receipt = Receipt(
                receipt_id=f"receipt-{seq}",
                state=ReceiptState.ACCEPTED,
                operation="memory.approve",
                principal_key_id=principal.key_id,
                owner_id=principal.owner_id,
                client_event_id=f"mem.{approved.revision_id}",
                payload_hash=payload_hash(
                    {
                        "content": approved.content,
                        "evidence": [link.to_wire() for link in approved.evidence],
                    }
                ),
                received_at=approved.recorded_at,
                server_seq=seq,
                stages=Stages(canonical_committed=True),
                degradations=(
                    Degradation(
                        "projections_pending", "the asserted revision is not yet indexed"
                    ),
                ),
                effective_scope=scope,
                memory_id=memory_id,
                revision_id=approved.revision_id,
                lifecycle=Lifecycle.ASSERTED.value,
            )
            self._store.receipts.append(receipt)
            return receipt

        return self._transaction(
            body, touched=("units", "revisions", "revision_order", "receipts", "outbox")
        )

    def retract_memory(self, credential: Credential, memory_id: str, *, reason: str) -> Receipt:
        """Retract a memory: block canonical reads now, invalidate derived state within budget.

        The tombstone deliberately carries no original text.
        """
        principal = self._authenticate(credential)
        scope = self._scope(principal, RequestedScope(), Action.MEMORY_RETRACT)
        unit = self._store.units.get(memory_id)
        if unit is None:
            raise RecordNotFound("unknown memory_id", memory_id=memory_id)
        self._require_in_scope(principal, scope, unit.space_id, unit.project_id)

        pending = ("records_fts", "cjk_bigrams", "embeddings", "graph_edges", "client_cache")

        def body() -> Receipt:
            seq = self._store.next_seq("server_seq")
            tombstone = Tombstone(
                memory_id=memory_id,
                owner_id=unit.owner_id,
                space_id=unit.space_id,
                project_id=unit.project_id,
                kind=unit.kind,
                reason=reason,
                created_at=self.now_iso(),
                receipt_id=f"receipt-{seq}",
            )
            self._store.units[memory_id] = replace(
                unit, lifecycle=Lifecycle.RETRACTED, server_seq=seq
            )
            self._store.tombstones[memory_id] = tombstone
            self._store.projection_deadlines[memory_id] = (
                self.now() + PROJECTION_INVALIDATION_SECONDS
            )
            self._invalidate_cache(memory_id)
            receipt = Receipt(
                receipt_id=f"receipt-{seq}",
                state=ReceiptState.ACCEPTED,
                operation="memory.retract",
                principal_key_id=principal.key_id,
                owner_id=principal.owner_id,
                client_event_id=f"retract.{memory_id}",
                payload_hash=payload_hash({"memory_id": memory_id, "reason": reason}),
                received_at=tombstone.created_at,
                server_seq=seq,
                stages=Stages(canonical_committed=True),
                degradations=(
                    Degradation(
                        "projections_invalidating",
                        "canonical reads are blocked now; derived state is still being cleared",
                    ),
                ),
                effective_scope=scope,
                derived_pending=pending,
                memory_id=memory_id,
                lifecycle=Lifecycle.RETRACTED.value,
            )
            self._store.receipts.append(receipt)
            return receipt

        return self._transaction(
            body,
            touched=("units", "tombstones", "projection_deadlines", "cache", "receipts"),
        )

    def erase_memory(self, credential: Credential, memory_id: str) -> ErasureManifest:
        """Erase a memory and return the replayable erasure plan.

        The plan is the artifact an operator replays after restoring a backup, so the erasure
        survives a restore it did not participate in.
        """
        principal = self._authenticate(credential)
        scope = self._scope(principal, RequestedScope(), Action.MEMORY_ERASE)
        unit = self._store.units.get(memory_id)
        if unit is None:
            raise RecordNotFound("unknown memory_id", memory_id=memory_id)
        self._require_in_scope(principal, scope, unit.space_id, unit.project_id)

        deferred = tuple(
            sorted(
                {f"offline_device:{item.device_id}" for item in self._store.principals.all()}
                - {f"offline_device:{principal.device_id}"}
            )
        ) + ("offline_backup:retention_window",)

        def body() -> ErasureManifest:
            seq = self._store.next_seq("manifest")
            manifest = ErasureManifest(
                manifest_id=f"erase-{seq}",
                memory_id=memory_id,
                targets=ERASURE_TARGETS,
                store_side_targets=ERASURE_TARGETS,
                pending_targets=ERASURE_TARGETS,
                deferred_targets=deferred,
                created_at=self.now_iso(),
                complete=False,
            )
            self._apply_erasure(memory_id)
            self._store.erasures[manifest.manifest_id] = manifest
            return manifest

        return self._transaction(
            body, touched=("units", "revisions", "erasures", "projection_deadlines", "cache")
        )

    def _apply_erasure(self, memory_id: str) -> None:
        """Mark a memory and every revision of it unreachable, and drop cached copies."""
        unit = self._store.units.get(memory_id)
        if unit is not None and unit.lifecycle is not Lifecycle.ERASED:
            self._store.units[memory_id] = replace(
                unit, lifecycle=Lifecycle.ERASED, server_seq=self._store.next_seq("server_seq")
            )
        for revision_id in self._store.revision_order.get(memory_id, []):
            revision = self._store.revisions.get(revision_id)
            if revision is not None and not revision.erased:
                self._store.revisions[revision_id] = replace(revision, erased=True)
        self._store.projection_deadlines[memory_id] = self.now() + PROJECTION_INVALIDATION_SECONDS
        self._invalidate_cache(memory_id)

    def _invalidate_cache(self, memory_id: str) -> None:
        """Drop every cached copy of a memory."""
        for key, entries in list(self._store.cache.items()):
            self._store.cache[key] = [item for item in entries if item.memory_id != memory_id]

    def replay_erasure(self, manifest: ErasureManifest) -> ErasureReplay:
        """Replay an erasure manifest.  Idempotent, and never resurrects a memory.

        Args:
            manifest: A manifest previously produced by :meth:`erase_memory`.  It is passed in
                because after a restore the store may no longer know about it.

        Returns:
            The replay outcome; ``remaining`` covers store-side targets only.
        """
        unit = self._store.units.get(manifest.memory_id)
        already = unit is not None and unit.lifecycle is Lifecycle.ERASED and all(
            self._store.revisions[item].erased
            for item in self._store.revision_order.get(manifest.memory_id, [])
            if item in self._store.revisions
        )

        def body() -> None:
            self._apply_erasure(manifest.memory_id)
            self._store.erasures[manifest.manifest_id] = manifest.with_pending(
                (), manifest.deferred_targets
            )

        self._transaction(
            body, touched=("units", "revisions", "erasures", "projection_deadlines", "cache")
        )
        return ErasureReplay(
            status="already_applied" if already else "replayed",
            manifest_id=manifest.manifest_id,
            remaining=(),
        )

    # --------------------------------------------------------------------------- read path

    def read_face(self, credential: Credential, face: Face, request: FaceRequest) -> FaceResult:
        """Serve one read face inside the effective scope.

        Args:
            credential: The caller's credential.
            face: Which surface is being read.
            request: The read request.

        Returns:
            The result, with the scope it was served under and any named degradation.

        Raises:
            FaceNotGranted: If the principal holds no grant for the action this face needs.
            ScopeNotGranted: If the request named scope outside the grants, or asked for a record
                outside them.
            RetractedRecordNotReadable: If a by-id read targets a retracted memory.
            ErasedRecordNotReadable: If a by-id read targets an erased memory.
        """
        principal = self._authenticate(credential)
        resolved = Face(face)
        required = FACE_ACTION[resolved]
        scope = self._scope(principal, request.requested_scope, required)
        degradations: List[Degradation] = []

        if resolved is Face.GRAPH:
            degradations.append(
                Degradation(
                    "graph_projection_not_implemented",
                    "relations are a P4 concern; this face returns no fabricated edges",
                )
            )
            return FaceResult(
                face=resolved,
                items=[],
                effective_scope=scope,
                denied=scope.denied,
                degradations=tuple(degradations),
            )

        if resolved is Face.GET:
            items = self._get_items(principal, scope, request)
            if not (request.memory_id or request.revision_id):
                degradations.append(
                    Degradation("get_requires_id", "the get face reads one record by id")
                )
        elif resolved is Face.CACHE:
            items = self._cache_items(principal, scope, request)
            degradations.append(
                Degradation(
                    "offline_cache_only",
                    "served from the local cache; content may be stale",
                )
            )
        elif resolved is Face.HISTORY:
            items = self._history_items(principal, scope, request)
        else:
            items = self._collect_items(principal, scope, request, resolved)

        self._remember(principal.key_id, items)
        return FaceResult(
            face=resolved,
            items=items,
            effective_scope=scope,
            denied=scope.denied,
            stale=resolved is Face.CACHE,
            served_from_cache=resolved is Face.CACHE,
            degradations=tuple(degradations),
        )

    def _require_in_scope(
        self, principal: Principal, scope: EffectiveScope, space_id: str, project_id: str
    ) -> None:
        """Refuse when a record is outside the effective scope.

        Raises:
            ScopeNotGranted: Always, when the record is out of scope.
        """
        if not scope.covers(space_id, project_id):
            raise ScopeNotGranted(
                "the record is outside the caller's effective scope",
                space_id=space_id,
                project_id=project_id,
                effective_spaces=list(scope.spaces),
                effective_projects=list(scope.projects),
            )

    def _visible(self, principal: Principal, scope: EffectiveScope, unit: MemoryUnit) -> bool:
        """Return whether a unit is visible to a reader under its server-derived visibility."""
        return visible_to(
            owner_id=unit.owner_id,
            space_id=unit.space_id,
            project_id=unit.project_id,
            visibility=self._store.effective_visibility(unit),
            actor_agent_id=unit.actor_agent_id,
            reader_owner_id=principal.owner_id,
            reader_actor_agent_id=principal.actor_agent_id,
            scope=scope,
        )

    def _item(self, unit: MemoryUnit, revision: Revision) -> FaceItem:
        """Render one served revision.

        ``lifecycle`` is the revision's own state (so history shows the state machine), while
        ``unit_lifecycle`` reports where the memory stands now - retracted or erased included.
        """
        return FaceItem(
            memory_id=unit.memory_id,
            revision_id=revision.revision_id,
            kind=revision.kind,
            content=revision.content,
            claim_status=revision.claim_status,
            lifecycle=revision.state,
            unit_lifecycle=unit.lifecycle,
            project_id=unit.project_id,
            space_id=unit.space_id,
            visibility=self._store.effective_visibility(unit),
            actor_agent_id=unit.actor_agent_id,
        )

    def _get_items(
        self, principal: Principal, scope: EffectiveScope, request: FaceRequest
    ) -> List[FaceItem]:
        """Read one record by ``memory_id`` or ``revision_id``."""
        if request.revision_id:
            revision = self._store.revisions.get(request.revision_id)
            if revision is None:
                raise RecordNotFound("unknown revision_id", revision_id=request.revision_id)
            unit = self._store.units.get(revision.memory_id)
        elif request.memory_id:
            unit = self._store.units.get(request.memory_id)
            revision = self._store.revisions[unit.current_revision] if unit else None
        else:
            return []
        if unit is None or revision is None:
            raise RecordNotFound("unknown record")
        self._require_in_scope(principal, scope, unit.space_id, unit.project_id)
        if not self._visible(principal, scope, unit):
            raise RecordNotFound("unknown record")
        if unit.lifecycle is Lifecycle.ERASED or revision.erased:
            raise ErasedRecordNotReadable("the record was erased", memory_id=unit.memory_id)
        if unit.lifecycle is Lifecycle.RETRACTED:
            raise RetractedRecordNotReadable(
                "the record was retracted; canonical reads are blocked", memory_id=unit.memory_id
            )
        return [self._item(unit, revision)]

    def _history_items(
        self, principal: Principal, scope: EffectiveScope, request: FaceRequest
    ) -> List[FaceItem]:
        """Read every revision of a memory.

        Retracted revisions stay readable here (that is what the separate ``history`` grant is
        for); erased ones never do.
        """
        if request.memory_id:
            unit = self._store.units.get(request.memory_id)
            if unit is None:
                raise RecordNotFound("unknown memory_id", memory_id=request.memory_id)
            self._require_in_scope(principal, scope, unit.space_id, unit.project_id)
            if not self._visible(principal, scope, unit):
                raise RecordNotFound("unknown memory_id")
            if unit.lifecycle is Lifecycle.ERASED:
                raise ErasedRecordNotReadable("the record was erased", memory_id=unit.memory_id)
            revisions = self._store.revisions_of(unit.memory_id)
            return [
                self._item(unit, revision)
                for revision in revisions
                if not revision.erased and request.matches(revision.content)
            ]

        items: List[FaceItem] = []
        for unit in self._store.units.values():
            if not self._in_reader_scope(principal, scope, unit):
                continue
            if unit.lifecycle is Lifecycle.ERASED:
                continue
            for revision in self._store.revisions_of(unit.memory_id):
                if revision.erased or not request.matches(revision.content):
                    continue
                items.append(self._item(unit, revision))
        return items

    def _collect_items(
        self,
        principal: Principal,
        scope: EffectiveScope,
        request: FaceRequest,
        face: Face,
    ) -> List[FaceItem]:
        """Collect items for search, bundle and export faces."""
        items: List[FaceItem] = []
        for unit in self._store.units.values():
            if not self._in_reader_scope(principal, scope, unit):
                continue
            if unit.lifecycle in (Lifecycle.RETRACTED, Lifecycle.ERASED, Lifecycle.REJECTED):
                continue
            if face is Face.SEARCH and unit.lifecycle is not Lifecycle.ASSERTED:
                continue
            revision = self._store.revisions[unit.current_revision]
            if revision.erased or not request.matches(revision.content):
                continue
            items.append(self._item(unit, revision))
            if len(items) >= request.limit:
                break
        return items

    def _in_reader_scope(
        self, principal: Principal, scope: EffectiveScope, unit: MemoryUnit
    ) -> bool:
        """Return whether a unit is in scope *and* visible to the reader."""
        if unit.owner_id != principal.owner_id:
            return False
        if not scope.covers(unit.space_id, unit.project_id):
            return False
        return self._visible(principal, scope, unit)

    def _cache_items(
        self, principal: Principal, scope: EffectiveScope, request: FaceRequest
    ) -> List[FaceItem]:
        """Serve previously delivered items from the local cache, marked stale."""
        items: List[FaceItem] = []
        seen: set = set()
        for entry in self._store.cache.get(principal.key_id, []):
            if entry.memory_id in seen:
                continue
            revision = self._store.revisions.get(entry.revision_id)
            unit = self._store.units.get(entry.memory_id)
            if revision is None or unit is None:
                continue
            if unit.lifecycle in (Lifecycle.RETRACTED, Lifecycle.ERASED):
                continue
            if not self._in_reader_scope(principal, scope, unit):
                continue
            if not request.matches(revision.content):
                continue
            seen.add(entry.memory_id)
            items.append(self._item(unit, revision))
        return items

    def _remember(self, key_id: str, items: Sequence[FaceItem]) -> None:
        """Record what a reader was served, so the cache face can be probed for staleness."""
        entries = self._store.cache.setdefault(key_id, [])
        known = {(entry.memory_id, entry.revision_id) for entry in entries}
        now = self.now()
        for item in items:
            if (item.memory_id, item.revision_id) in known:
                continue
            entries.append(
                CacheEntry(
                    memory_id=item.memory_id, revision_id=item.revision_id, served_at=now
                )
            )

    # -------------------------------------------------------------------- changelog cursors

    def changelog_cursor(self, credential: Credential, *, space_id: Optional[str] = None) -> str:
        """Mint an opaque cursor bound to ``(owner, space, permission_epoch)``.

        The token is random and resolved server-side; nothing about the owner or space can be
        read out of it.
        """
        principal = self._authenticate(credential)
        requested = RequestedScope(spaces=(space_id,)) if space_id else RequestedScope()
        scope = self._scope(principal, requested, Action.READ)
        if not scope.spaces:
            raise FaceNotGranted("the principal has no readable space to bind a cursor to")
        bound_space = scope.spaces[0]
        token = secrets.token_urlsafe(24)
        self._store.cursors[token] = CursorRecord(
            cursor=token,
            principal_key_id=principal.key_id,
            owner_id=principal.owner_id,
            space_id=bound_space,
            epoch=self._store.epoch(principal.owner_id, bound_space),
            after_seq=self._store.current_seq(),
            created_at=self.now_iso(),
        )
        return token

    def read_changelog(self, credential: Credential, cursor: str) -> CursorReadResult:
        """Read deltas after a cursor, or ask the caller to resnapshot.

        A cursor whose permission epoch has moved, or which belongs to another principal, yields
        ``resnapshot_required`` with **no** deltas - never a partial, possibly over-broad list.
        """
        principal = self._authenticate(credential)
        record = self._store.cursors.get(cursor)
        if (
            record is None
            or record.principal_key_id != principal.key_id
            or record.owner_id != principal.owner_id
        ):
            return CursorReadResult(status=RESNAPSHOT_REQUIRED, cursor=cursor)
        epoch = self._store.epoch(record.owner_id, record.space_id)
        if record.epoch != epoch:
            return CursorReadResult(status=RESNAPSHOT_REQUIRED, cursor=cursor, epoch=epoch)

        scope = self._scope(
            principal, RequestedScope(spaces=(record.space_id,)), Action.READ
        )
        deltas: List[ChangelogDelta] = []
        for unit in self._store.units.values():
            if unit.space_id != record.space_id or unit.server_seq <= record.after_seq:
                continue
            if not self._in_reader_scope(principal, scope, unit):
                continue
            if unit.lifecycle is Lifecycle.ERASED:
                continue
            deltas.append(
                ChangelogDelta(
                    server_seq=unit.server_seq,
                    memory_id=unit.memory_id,
                    revision_id=unit.current_revision,
                    kind=unit.kind,
                    lifecycle=unit.lifecycle,
                    operation="memory.changed",
                )
            )
        deltas.sort(key=lambda delta: delta.server_seq)
        return CursorReadResult(
            status=CHANGELOG_OK, cursor=cursor, deltas=tuple(deltas), epoch=epoch
        )

    # ------------------------------------------------------------------------------ probing

    def stats(self) -> Dict[str, int]:
        """Return countable state (never anything derived from a secret)."""
        return self._store.stats()

    def conflicts(self) -> Tuple[ConflictRecord, ...]:
        """Return every recorded revision conflict."""
        return tuple(self._store.conflicts)

    def tombstone_for(self, memory_id: str) -> Tombstone:
        """Return the tombstone of a retracted or erased memory."""
        return self._store.tombstones[memory_id]

    def projection_deadline(self, memory_id: str) -> float:
        """Return the epoch seconds by which derived state must be invalidated."""
        return self._store.projection_deadlines.get(memory_id, 0.0)

    def revisions_of(self, memory_id: str) -> Tuple[Revision, ...]:
        """Return every revision of a memory (test/introspection helper)."""
        return tuple(self._store.revisions_of(memory_id))

    def export_snapshot(self) -> Mapping[str, Any]:
        """Return a JSON-safe snapshot of durable state (credential hashes only)."""
        return self._store.snapshot()

    def import_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        """Restore durable state from a snapshot.

        The receipt ledger and cache are re-derived rather than restored: dedup reads the
        canonical event log, so a failed restore cannot resurrect an accepted event.
        """
        self._store.restore(snapshot)


__all__ = [
    "CHANGELOG_OK",
    "PROJECTION_INVALIDATION_SECONDS",
    "RESNAPSHOT_REQUIRED",
    "InProcessAdapter",
]
