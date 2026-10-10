"""N1-N11 negative tests for the P2-01 service contract layer.

Project discipline: these tests were written **before** ``memory_tool.service_contract`` existed.
Each test maps to one row of the N1-N11 table in ``docs/design/p2-write-path-minimal-loop.md`` §5.
Where the full scenario needs the P2-02 runtime (``service.sqlite3``) or the P2-03 cross-device
clients, the test asserts the contract-level invariant that is decidable today and names the
deferred part in the docstring, so a green run cannot be mistaken for a completed end-to-end claim.

The three rules called out explicitly for this delivery are marked with ``HEADLINE``.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from memory_tool.service_contract import (
    CONTRACT_VERSION,
    UNASSIGNED,
    Action,
    ClaimStatus,
    ClientOutbox,
    Credential,
    ErasedRecordNotReadable,
    EventSubmission,
    EvidenceLink,
    EvidenceRequiredForAssertion,
    Face,
    FaceNotGranted,
    FaceRequest,
    IdempotencyConflict,
    IdentityFieldRejected,
    InProcessAdapter,
    MemoryProposal,
    OutboxState,
    ProjectRegistry,
    ReceiptState,
    RecordNotFound,
    Registration,
    RequestedScope,
    RetractedRecordNotReadable,
    RevisionConflict,
    ScopeNotGranted,
    Unauthenticated,
    Visibility,
)

OBSERVED = "2026-10-11T00:00:00Z"
EVIDENCE = (EvidenceLink(source_id="src-1", revision="rev-1", span="message-42"),)

#: Ground truth used to count leakage from outside.  The tests count these tokens in the
#: serialized output of every face instead of trusting a count the adapter reports about itself.
FOREIGN_TOKENS = ("FKTOKENALPHA", "FKTOKENBETA")

#: (owner, space, project, source_app) for each registered device credential.  ``key-a`` and
#: ``key-a2`` share an owner (they are two devices of one person); the others do not.
HOME = {
    "key-a": ("owner-a", "space-a", "proj-a", "codex"),
    "key-a2": ("owner-a", "space-a", "proj-a", "codex"),
    "key-other": ("owner-a", "space-a", "proj-a", "codex"),
    "key-b": ("owner-b", "space-b", "proj-b", "kimi-code"),
    "key-c": ("owner-c", "space-c", "proj-c", "grok"),
}


@dataclass
class Lab:
    """Three isolated owners, a second device on one of them, and a second actor."""

    svc: InProcessAdapter
    a: Registration
    a2: Registration
    b: Registration
    c: Registration
    other_actor: Registration

    def home(self, reg: Registration) -> Tuple[str, str, str, str]:
        return HOME[reg.key_id]

    def submission(
        self,
        reg: Registration,
        client_event_id: str,
        content: str,
        *,
        source_seq: int = 1,
        observed_at: str = OBSERVED,
        thread_id: str = "th-1",
        project_id: Optional[str] = None,
        requested_scope: Optional[RequestedScope] = None,
    ) -> EventSubmission:
        _, _, project, app = self.home(reg)
        return EventSubmission(
            client_event_id=client_event_id,
            content=content,
            source_instance=reg.device_id,
            thread_id=thread_id,
            source_seq=source_seq,
            role="user",
            observed_at=observed_at,
            source_app=app,
            project_id=project_id or project,
            requested_scope=requested_scope or RequestedScope(projects=(project,)),
            contract_version=CONTRACT_VERSION,
        )

    def memory(
        self,
        reg: Registration,
        content: str,
        *,
        kind: str = "fact",
        memory_id: Optional[str] = None,
        base_revision: Optional[str] = None,
        claim_status: ClaimStatus = ClaimStatus.UNDECLARED,
        evidence: Sequence[EvidenceLink] = (),
        requested_scope: Optional[RequestedScope] = None,
    ) -> MemoryProposal:
        _, _, project, _ = self.home(reg)
        return MemoryProposal(
            kind=kind,
            content=content,
            claim_status=claim_status,
            memory_id=memory_id,
            base_revision=base_revision,
            evidence=tuple(evidence),
            project_id=project,
            requested_scope=requested_scope or RequestedScope(projects=(project,)),
            contract_version=CONTRACT_VERSION,
        )

    def write(self, reg: Registration, content: str, *, approve: bool = False, **kwargs: Any):
        receipt = self.svc.propose_memory(reg.credential, self.memory(reg, content, **kwargs))
        if approve:
            self.svc.approve_memory(
                reg.credential,
                receipt.memory_id,
                base_revision=receipt.revision_id,
                evidence=EVIDENCE,
            )
        return receipt


@dataclass(frozen=True)
class ForeignWrites:
    """Ground truth written outside the reader's scope, with the ids the reader might guess."""

    tokens: Tuple[str, ...]
    memory_ids: Tuple[str, ...]
    revision_ids: Tuple[str, ...]


@pytest.fixture
def svc() -> InProcessAdapter:
    return InProcessAdapter()


@pytest.fixture
def lab(svc: InProcessAdapter) -> Lab:
    a = svc.register_device("owner-a", "device-m1", key_id="key-a", actor_agent_id="agent-1")
    a2 = svc.register_device("owner-a", "device-m1b", key_id="key-a2", actor_agent_id="agent-1")
    b = svc.register_device("owner-b", "device-m3", key_id="key-b", actor_agent_id="agent-1")
    c = svc.register_device("owner-c", "device-nas34", key_id="key-c", actor_agent_id="agent-1")
    other = svc.register_device(
        "owner-a", "device-m1c", key_id="key-other", actor_agent_id="agent-2"
    )
    for reg in (a, a2, b, c, other):
        owner, space, project, _ = HOME[reg.key_id]
        for action in Action:
            svc.grant(owner, space, project, action)
    return Lab(svc=svc, a=a, a2=a2, b=b, c=c, other_actor=other)


def foreign_writes(svc: InProcessAdapter, lab: Lab) -> ForeignWrites:
    """Write memories only ``lab.b`` and ``lab.c`` may read, and return their tokens and ids."""
    memory_ids = []
    revision_ids = []
    for reg in (lab.b, lab.c):
        for token in FOREIGN_TOKENS:
            receipt = lab.write(reg, f"foreign memory {token}", approve=True)
            memory_ids.append(receipt.memory_id)
            revision_ids.append(receipt.revision_id)
    return ForeignWrites(
        tokens=FOREIGN_TOKENS,
        memory_ids=tuple(memory_ids),
        revision_ids=tuple(revision_ids),
    )


def read(svc: InProcessAdapter, reg: Registration, face: Face, **request: Any):
    """Read one face as ``reg``; a short helper so assertions stay on one line."""
    return svc.read_face(reg.credential, face, FaceRequest(**request))


def leaks_in(value: Any) -> int:
    """Count foreign ground-truth tokens in a serialized result or refusal."""
    if hasattr(value, "to_wire"):
        blob = json.dumps(value.to_wire())
    elif isinstance(value, str):
        blob = value
    else:
        blob = json.dumps(value)
    return sum(token in blob for token in FOREIGN_TOKENS)


# --------------------------------------------------------------------------------------
# HEADLINE 1 - a client must not be able to declare its own owner/space (invariant I4)
# --------------------------------------------------------------------------------------


def test_headline_client_declared_owner_is_rejected_at_the_wire_boundary(lab: Lab) -> None:
    """A payload carrying ``owner_id`` cannot even be parsed into a submission."""
    payload = lab.submission(lab.a, "ce-id-1", "hello").to_wire()
    payload["owner_id"] = "owner-b"

    with pytest.raises(IdentityFieldRejected) as excinfo:
        EventSubmission.from_wire(payload)

    assert excinfo.value.code == "identity_field_rejected"
    assert excinfo.value.details["field"] == "owner_id"
    assert excinfo.value.details["reason"] == "server_owned"


def test_headline_client_declared_space_is_rejected_at_any_depth(lab: Lab) -> None:
    """``space_id`` is server-owned, including inside a nested proposal."""
    payload = lab.submission(lab.a, "ce-id-2", "hello").to_wire()
    payload["requested_scope"] = {"spaces": ["space-b"], "projects": []}
    payload["memory"] = {"kind": "fact", "content": "sneaky", "space_id": "space-b"}

    with pytest.raises(IdentityFieldRejected) as excinfo:
        EventSubmission.from_wire(payload)

    assert excinfo.value.details["field"] == "memory.space_id"


@pytest.mark.parametrize(
    "field, value",
    [
        ("visibility", "owner_shared"),
        ("server_seq", 99),
        ("receipt_state", "accepted"),
        ("hashed_secret", "deadbeef"),
    ],
)
def test_headline_client_declared_visibility_and_server_fields_are_rejected(
    lab: Lab, field: str, value: Any
) -> None:
    """Visibility, receipt state, sequence numbers and key material are all computed."""
    payload = lab.submission(lab.a, f"ce-{field}", "hello").to_wire()
    payload[field] = value
    with pytest.raises(IdentityFieldRejected):
        EventSubmission.from_wire(payload)


def test_headline_credential_bound_identity_cannot_be_impersonated(svc, lab: Lab) -> None:
    """``device_id``/``actor_agent_id`` come from the credential, never from the payload."""
    payload = lab.submission(lab.a, "ce-actor", "hello").to_wire()
    payload["source"]["actor_agent_id"] = "agent-2"
    with pytest.raises(IdentityFieldRejected) as excinfo:
        EventSubmission.from_wire(payload)
    assert excinfo.value.details["field"] == "source.actor_agent_id"

    payload["source"] = {"source_app": "codex", "thread_id": "th-1", "device_id": "device-m3"}
    with pytest.raises(IdentityFieldRejected) as excinfo:
        EventSubmission.from_wire(payload)
    assert excinfo.value.details["field"] == "source.device_id"

    svc.append_event(lab.a.credential, lab.submission(lab.a, "ce-actor-2", "hello"))
    stored = svc.get_event(lab.a.credential, "ce-actor-2")
    assert stored.device_id == "device-m1"
    assert stored.actor_agent_id == "agent-1"


def test_headline_named_foreign_scope_is_rejected_not_silently_empty(svc, lab: Lab) -> None:
    """Asking for another space by name is refused, and never widens the effective scope."""
    with pytest.raises(ScopeNotGranted) as excinfo:
        svc.propose_memory(
            lab.a.credential,
            lab.memory(
                lab.a,
                "attempted cross-space write",
                requested_scope=RequestedScope(spaces=("space-b",), projects=()),
            ),
        )

    assert excinfo.value.details["requested"] == ["space-b"]
    assert excinfo.value.details["granted_spaces"] == ["space-a"]


# --------------------------------------------------------------------------------------
# HEADLINE 2 - same idempotency key with a different payload must conflict (N2)
# --------------------------------------------------------------------------------------


def test_headline_n2_same_idempotency_key_with_different_payload_conflicts(svc, lab: Lab) -> None:
    first = svc.append_event(lab.a.credential, lab.submission(lab.a, "ce-dup", "payload one"))
    before = svc.stats()

    with pytest.raises(IdempotencyConflict) as excinfo:
        svc.append_event(lab.a.credential, lab.submission(lab.a, "ce-dup", "payload two"))

    assert excinfo.value.stored_receipt.server_seq == first.server_seq
    assert (
        excinfo.value.details["stored_payload_hash"]
        != excinfo.value.details["submitted_payload_hash"]
    )
    assert excinfo.value.details["client_event_id"] == "ce-dup"
    assert svc.stats() == before, "a conflicting replay must not overwrite or add anything"
    assert svc.get_event(lab.a.credential, "ce-dup").content == "payload one"


def test_n2_conflict_is_per_principal_not_global(svc, lab: Lab) -> None:
    """The same ``client_event_id`` from two principals is two independent commits."""
    first = svc.append_event(lab.a.credential, lab.submission(lab.a, "ce-shared-id", "from a"))
    second = svc.append_event(lab.b.credential, lab.submission(lab.b, "ce-shared-id", "from b"))

    assert first.server_seq != second.server_seq
    assert svc.stats()["events"] == 2


# --------------------------------------------------------------------------------------
# HEADLINE 3 - claim_status defaults to undeclared, never asserted
# --------------------------------------------------------------------------------------


def test_headline_claim_status_default_is_undeclared(svc, lab: Lab) -> None:
    assert MemoryProposal(kind="fact", content="x").claim_status is ClaimStatus.UNDECLARED

    parsed = MemoryProposal.from_wire({"kind": "fact", "content": "no claim status given"})
    assert parsed.claim_status is ClaimStatus.UNDECLARED

    parsed_null = MemoryProposal.from_wire(
        {"kind": "fact", "content": "explicit null", "claim_status": None}
    )
    assert parsed_null.claim_status is ClaimStatus.UNDECLARED

    receipt = lab.write(lab.a, "silent on claims", approve=False)
    item = read(svc, lab.a, Face.GET, memory_id=receipt.memory_id).items[0]
    assert item.claim_status is ClaimStatus.UNDECLARED
    assert item.claim_status is not ClaimStatus.ASSERTED


def test_headline_undeclared_is_not_upgraded_by_being_read_again(svc, lab: Lab) -> None:
    """Repeated reads must not promote a claim; only an evidence-bearing approval does."""
    receipt = lab.write(lab.a, "REPEATEDCLAIM", approve=True)
    for _ in range(5):
        svc.read_face(lab.a.credential, Face.SEARCH, FaceRequest(query="REPEATEDCLAIM"))
    item = read(svc, lab.a, Face.GET, memory_id=receipt.memory_id).items[0]
    assert item.claim_status is ClaimStatus.ASSERTED
    assert item.lifecycle == "asserted"


def test_headline_asserting_without_evidence_is_refused(svc, lab: Lab) -> None:
    with pytest.raises(EvidenceRequiredForAssertion):
        svc.propose_memory(
            lab.a.credential,
            lab.memory(lab.a, "asserted without proof", claim_status=ClaimStatus.ASSERTED),
        )

    proposed = lab.write(lab.a, "proposed without proof", claim_status=ClaimStatus.PROPOSED)
    with pytest.raises(EvidenceRequiredForAssertion):
        svc.approve_memory(
            lab.a.credential, proposed.memory_id, base_revision=proposed.revision_id, evidence=()
        )


# --------------------------------------------------------------------------------------
# N1 - 100 identical submissions, one semantic commit
# --------------------------------------------------------------------------------------


def test_n1_repeated_identical_submission_yields_one_commit(svc, lab: Lab) -> None:
    receipts = [
        svc.append_event(lab.a.credential, lab.submission(lab.a, "ce-1", "written once"))
        for _ in range(100)
    ]

    assert svc.stats()["events"] == 1
    assert len({receipt.server_seq for receipt in receipts}) == 1
    assert len({receipt.receipt_id for receipt in receipts}) == 1
    assert receipts[0].replayed is False
    assert all(receipt.replayed for receipt in receipts[1:])
    assert all(receipt.state is ReceiptState.ACCEPTED for receipt in receipts)
    # accepted means the original event is persisted, not that it is extracted or searchable.
    assert receipts[0].stages.canonical_committed is True
    assert receipts[0].stages.lexical_ready is False
    assert receipts[0].stages.semantic_ready is False
    assert "projections_pending" in {item.name for item in receipts[0].degradations}


# --------------------------------------------------------------------------------------
# N3 - offline outbox replay: shuffle, interruption, client-side acceptance
# --------------------------------------------------------------------------------------


def test_n3_offline_outbox_replay_survives_shuffle_and_interruption(svc, lab: Lab) -> None:
    """Contract-level part of N3; the physical 24 h outage is P2-03 (see module docstring)."""
    outbox = ClientOutbox()
    for index in range(24):
        outbox.enqueue(lab.submission(lab.a, f"ce-{index}", f"offline {index}"))

    assert {item.state for item in outbox.items} == {OutboxState.QUEUED}

    # The process dies mid-flush: in-flight state is discarded and replay restarts from the
    # durable outbox, in a different order, assuming no server-side progress.
    order = list(range(24))
    random.Random(7).shuffle(order)
    for index in order[:11]:
        outbox.acknowledge(svc.append_event(lab.a.credential, outbox.get(f"ce-{index}").submission))

    for _ in range(3):
        for item in list(outbox.items):
            if item.state is OutboxState.ACKNOWLEDGED:
                continue
            outbox.acknowledge(svc.append_event(lab.a.credential, item.submission))

    assert svc.stats()["events"] == 24
    assert {item.state for item in outbox.items} == {OutboxState.ACKNOWLEDGED}
    assert all(svc.receipt_for(lab.a.credential, f"ce-{index}") is not None for index in range(24))


def test_n3_server_seq_is_arrival_order_not_client_clock_or_source_seq(svc, lab: Lab) -> None:
    late = svc.append_event(
        lab.a.credential,
        lab.submission(
            lab.a, "ce-late", "arrived first", source_seq=9, observed_at="2020-01-01T00:00:00Z"
        ),
    )
    early = svc.append_event(
        lab.a.credential,
        lab.submission(
            lab.a, "ce-early", "arrived second", source_seq=1, observed_at="2030-01-01T00:00:00Z"
        ),
    )

    assert late.server_seq < early.server_seq
    assert [receipt.observed_at for receipt in (late, early)] == [
        "2020-01-01T00:00:00Z",
        "2030-01-01T00:00:00Z",
    ]


def test_n3_offline_write_cannot_claim_accepted(lab: Lab) -> None:
    """A client may only ever self-report ``queued``; ``accepted`` is a server receipt."""
    assert not hasattr(OutboxState, "ACCEPTED")
    outbox = ClientOutbox()
    item = outbox.enqueue(lab.submission(lab.a, "ce-offline", "queued while offline"))
    assert item.state is OutboxState.QUEUED
    assert outbox.state_of("ce-offline") is OutboxState.QUEUED


# --------------------------------------------------------------------------------------
# N4 - two agents edit the same base revision: one wins, one gets a conflict
# --------------------------------------------------------------------------------------


def test_n4_concurrent_revision_edit_conflicts_and_keeps_both_proposals(svc, lab: Lab) -> None:
    first = lab.write(lab.a, "version one")
    approved = svc.approve_memory(
        lab.a.credential, first.memory_id, base_revision=first.revision_id, evidence=EVIDENCE
    )
    base = approved.revision_id

    winner = svc.propose_memory(
        lab.a.credential,
        lab.memory(
            lab.a, "version two from agent 1", memory_id=first.memory_id, base_revision=base
        ),
    )

    with pytest.raises(RevisionConflict) as excinfo:
        svc.propose_memory(
            lab.a2.credential,
            lab.memory(
                lab.a2, "version two from agent 2", memory_id=first.memory_id, base_revision=base
            ),
        )

    assert excinfo.value.current_revision == winner.revision_id
    assert excinfo.value.details["base_revision"] == base
    assert len(excinfo.value.details["proposals"]) == 2

    # No merge, no overwrite: both proposals stay readable by id.
    losing_revision = excinfo.value.details["proposals"][1]
    for revision_id in (winner.revision_id, losing_revision):
        svc.read_face(lab.a.credential, Face.GET, FaceRequest(revision_id=revision_id))

    conflicts = svc.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].memory_id == first.memory_id
    assert set(conflicts[0].revision_ids) == {winner.revision_id, losing_revision}

    # Neither unresolved proposal is served as a default high-confidence answer.
    default = svc.read_face(lab.a.credential, Face.SEARCH, FaceRequest(query="version two"))
    assert default.items == []

    history = svc.read_face(lab.a.credential, Face.HISTORY, FaceRequest(memory_id=first.memory_id))
    assert {item.lifecycle for item in history.items} >= {"asserted", "proposed"}


# --------------------------------------------------------------------------------------
# N5 - every read face refuses foreign scope; the leak count is zero, counted externally
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("face", list(Face))
def test_n5_foreign_scope_is_refused_on_every_face(svc, lab: Lab, face: Face) -> None:
    foreign = foreign_writes(svc, lab)
    request = FaceRequest(
        query=foreign.tokens[0],
        memory_id=foreign.memory_ids[0],
        revision_id=foreign.revision_ids[0],
        requested_scope=RequestedScope(projects=("proj-b",), spaces=("space-b",)),
    )

    with pytest.raises(ScopeNotGranted) as excinfo:
        svc.read_face(lab.a.credential, face, request)

    assert leaks_in(str(excinfo.value)) == 0


@pytest.mark.parametrize("face", [Face.GET, Face.HISTORY])
def test_n5_foreign_id_is_refused_by_id_faces(svc, lab: Lab, face: Face) -> None:
    """Knowing a foreign id is not authority: by-id reads refuse without echoing content."""
    foreign = foreign_writes(svc, lab)
    request = FaceRequest(memory_id=foreign.memory_ids[0], revision_id=foreign.revision_ids[0])

    with pytest.raises((ScopeNotGranted, RecordNotFound)) as excinfo:
        svc.read_face(lab.a.credential, face, request)

    assert leaks_in(str(excinfo.value)) == 0


def test_n5_leak_count_is_zero_on_every_face(svc, lab: Lab) -> None:
    """Unscoped (default) reads are the dangerous case; each face is counted separately."""
    foreign = foreign_writes(svc, lab)

    leaks: Dict[str, int] = {}
    for face in Face:
        request = FaceRequest(query=" ".join(foreign.tokens), limit=50)
        leaks[face.value] = leaks_in(svc.read_face(lab.a.credential, face, request))

    assert leaks == {face.value: 0 for face in Face}
    assert sum(leaks.values()) == 0


def test_n5_face_requires_its_own_action_not_just_membership(svc, lab: Lab) -> None:
    """Reading a project is not the same grant as exporting it.

    Grants are keyed on ``(owner, space, project, action)`` - not on the device - so revoking
    ``export`` takes that face away from every credential of the owner.
    """
    lab.write(lab.a, "EXPORTTOKEN", approve=True)
    assert svc.revoke_grant("owner-a", "space-a", "proj-a", Action.EXPORT) >= 1

    search = svc.read_face(lab.a.credential, Face.SEARCH, FaceRequest(query="EXPORTTOKEN"))
    assert len(search.items) == 1

    with pytest.raises(FaceNotGranted):
        svc.read_face(lab.a.credential, Face.EXPORT, FaceRequest(query="EXPORTTOKEN"))


def test_n5_invalid_or_revoked_credentials_are_refused(svc, lab: Lab) -> None:
    """Authentication failure must not leak whether the owner exists."""
    with pytest.raises(Unauthenticated):
        svc.append_event(
            Credential(key_id="key-a", secret="not-the-secret"),
            lab.submission(lab.a, "ce-forged", "forged"),
        )

    svc.revoke_key(lab.b.key_id)
    with pytest.raises(Unauthenticated):
        svc.append_event(lab.b.credential, lab.submission(lab.b, "ce-revoked", "revoked"))


def test_n5_agent_private_records_are_not_readable_by_a_sibling_agent(svc, lab: Lab) -> None:
    lab.write(lab.a, "PRIVATETOKEN", approve=True)

    hidden = svc.read_face(
        lab.other_actor.credential, Face.SEARCH, FaceRequest(query="PRIVATETOKEN")
    )
    assert hidden.items == []

    svc.set_visibility_policy("owner-a", "space-a", "proj-a", "agent-1", Visibility.PROJECT_SHARED)
    shared = svc.read_face(
        lab.other_actor.credential, Face.SEARCH, FaceRequest(query="PRIVATETOKEN")
    )
    assert len(shared.items) == 1


# --------------------------------------------------------------------------------------
# N6 - a model-declared owner/project cannot widen the effective scope
# --------------------------------------------------------------------------------------


def test_n6_self_reported_project_does_not_widen_scope(svc, lab: Lab) -> None:
    foreign_writes(svc, lab)

    proposal = MemoryProposal(
        kind="fact",
        content="FKTOKENALPHA leaked by self-report",
        project_id="proj-b",
        requested_scope=RequestedScope(projects=("proj-b",), spaces=("space-b",)),
    )
    with pytest.raises(ScopeNotGranted):
        svc.propose_memory(lab.a.credential, proposal)

    # Stored scope is the granted one, never the requested one.
    own = lab.write(lab.a, "OWNTOKEN", approve=True)
    item = svc.read_face(lab.a.credential, Face.GET, FaceRequest(memory_id=own.memory_id)).items[0]
    assert item.project_id == "proj-a"
    assert item.space_id == "space-a"


def test_n6_effective_scope_is_intersection_only(svc, lab: Lab) -> None:
    scope = svc.effective_scope(
        lab.a.credential,
        FaceRequest(requested_scope=RequestedScope(projects=("proj-a", "proj-b"))),
    )
    assert scope.projects == ("proj-a",)
    assert "proj-b" in scope.denied
    assert scope.owner_id == "owner-a"


# --------------------------------------------------------------------------------------
# N7 - project mapping: aliases, forks, environments, conflicts
# --------------------------------------------------------------------------------------


def test_n7_project_registry_never_guesses_from_a_directory_name() -> None:
    registry = ProjectRegistry()
    registry.register_project("proj-a", "owner-a", "space-a")
    registry.add_alias("proj-a", "git_remote", "github.com/org/repo")
    registry.add_alias("proj-a", "device_path", "/Users/m1/work/repo")
    registry.add_alias("proj-a", "device_path", "/Users/m3/work/repo")

    assert registry.resolve("git_remote", "github.com/org/repo") == "proj-a"
    assert registry.resolve("device_path", "/Users/m3/work/repo") == "proj-a"
    assert registry.resolve_path("/Users/m1/work/repo") == "proj-a"

    # A fork keeps the directory basename but is a different project until it is registered.
    assert registry.resolve_path("/Users/m1/work/repo-fork") == UNASSIGNED
    assert registry.resolve("git_remote", "github.com/other/repo") == UNASSIGNED
    assert registry.resolve("device_path", "/unregistered/repo") == UNASSIGNED

    registry.register_project("proj-fork", "owner-a", "space-a")
    registry.add_alias("proj-fork", "device_path", "/Users/m1/work/repo-fork")
    assert registry.resolve_path("/Users/m1/work/repo-fork") == "proj-fork"


def test_n7_alias_conflict_is_marked_unassigned_and_reviewable() -> None:
    registry = ProjectRegistry()
    registry.register_project("proj-a", "owner-a", "space-a")
    registry.register_project("proj-b", "owner-a", "space-b")
    registry.add_alias("proj-a", "git_remote", "github.com/org/repo")

    registry.add_alias("proj-b", "git_remote", "github.com/org/repo")

    assert registry.resolve("git_remote", "github.com/org/repo") == UNASSIGNED
    conflicts = registry.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].alias_value == "github.com/org/repo"
    assert set(conflicts[0].project_ids) == {"proj-a", "proj-b"}


def test_n7_environment_is_part_of_the_alias_key() -> None:
    registry = ProjectRegistry()
    registry.register_project("proj-a", "owner-a", "space-a")
    registry.add_alias("proj-a", "git_remote", "github.com/org/repo", environment="production")

    resolved = registry.resolve("git_remote", "github.com/org/repo", environment="production")
    assert resolved == "proj-a"
    assert registry.resolve("git_remote", "github.com/org/repo", environment="test") == UNASSIGNED


# --------------------------------------------------------------------------------------
# N8 - retraction blocks canonical reads and invalidates derived state
# --------------------------------------------------------------------------------------


def test_n8_retraction_blocks_reads_and_names_pending_projection_work(svc, lab: Lab) -> None:
    receipt = lab.write(lab.a, "RETRACTTOKEN", approve=True)
    search = svc.read_face(lab.a.credential, Face.SEARCH, FaceRequest(query="RETRACTTOKEN"))
    assert len(search.items) == 1
    svc.read_face(lab.a.credential, Face.CACHE, FaceRequest(query="RETRACTTOKEN"))

    retraction = svc.retract_memory(lab.a.credential, receipt.memory_id, reason="wrong fact")

    assert read(svc, lab.a, Face.SEARCH, query="RETRACTTOKEN").items == []
    assert read(svc, lab.a, Face.CACHE, query="RETRACTTOKEN").items == []
    with pytest.raises(RetractedRecordNotReadable):
        svc.read_face(lab.a.credential, Face.GET, FaceRequest(memory_id=receipt.memory_id))

    assert retraction.derived_pending, "the receipt must name what is still pending"
    assert "projections_invalidating" in {item.name for item in retraction.degradations}
    deadline = svc.projection_deadline(receipt.memory_id)
    assert 0 < deadline - svc.now() <= 300, "derived state must be invalidated within 5 minutes"


def test_n8_tombstone_keeps_no_original_text_and_history_stays_readable(svc, lab: Lab) -> None:
    receipt = lab.write(lab.a, "DISAPPEARINGTOKEN", approve=True)
    svc.retract_memory(lab.a.credential, receipt.memory_id, reason="wrong fact")

    tombstone = svc.tombstone_for(receipt.memory_id)
    assert leaks_in(json.dumps(tombstone.to_wire())) == 0
    assert "DISAPPEARINGTOKEN" not in json.dumps(tombstone.to_wire())
    assert tombstone.content_absent is True

    history = read(svc, lab.a, Face.HISTORY, memory_id=receipt.memory_id)
    assert [item.lifecycle for item in history.items] == ["proposed", "asserted"]
    assert {item.unit_lifecycle for item in history.items} == {"retracted"}
    assert history.items[-1].content == "DISAPPEARINGTOKEN"


# --------------------------------------------------------------------------------------
# N9 - restoring a backup must be followed by replaying the erasure list
# --------------------------------------------------------------------------------------


def test_n9_erasure_manifest_replay_prevents_resurrection(svc, lab: Lab) -> None:
    """Contract-level part of N9; the off-host restore itself is P1/P2-02."""
    receipt = lab.write(lab.a, "ERASEMETOKEN", approve=True)
    backup = svc.export_snapshot()

    manifest = svc.erase_memory(lab.a.credential, receipt.memory_id)
    assert manifest.targets, "erasure must enumerate the derived objects it covers"
    assert manifest.complete is False, "offline devices cannot be assumed reachable"
    assert read(svc, lab.a, Face.SEARCH, query="ERASEMETOKEN").items == []

    svc.import_snapshot(backup)
    resurrected = svc.read_face(
        lab.a.credential, Face.GET, FaceRequest(revision_id=receipt.revision_id)
    )
    assert resurrected.items[0].content == "ERASEMETOKEN"

    first = svc.replay_erasure(manifest)
    assert first.status == "replayed"
    assert first.remaining == ()
    assert read(svc, lab.a, Face.SEARCH, query="ERASEMETOKEN").items == []
    with pytest.raises(ErasedRecordNotReadable):
        svc.read_face(lab.a.credential, Face.GET, FaceRequest(revision_id=receipt.revision_id))

    again = svc.replay_erasure(manifest)
    assert again.status == "already_applied"


def test_n9_snapshot_and_restore_keep_idempotency(svc, lab: Lab) -> None:
    """Dedup is derived from the canonical log, so a restore cannot resurrect an event."""
    submission = lab.submission(lab.a, "ce-restore", "committed once")
    svc.append_event(lab.a.credential, submission)
    backup = svc.export_snapshot()

    svc.import_snapshot(backup)
    replay = svc.append_event(lab.a.credential, submission)

    assert replay.replayed is True
    assert svc.stats()["events"] == 1


def test_n9_snapshot_carries_no_plaintext_secret(svc, lab: Lab) -> None:
    snapshot = json.dumps(svc.export_snapshot(), sort_keys=True)
    assert lab.a.secret not in snapshot
    assert lab.b.secret not in snapshot
    assert lab.a.secret not in repr(svc.stats())


# --------------------------------------------------------------------------------------
# N10 - permission epoch changes invalidate old cursors
# --------------------------------------------------------------------------------------


def test_n10_stale_cursor_requires_resnapshot_and_returns_no_deltas(svc, lab: Lab) -> None:
    cursor = svc.changelog_cursor(lab.a.credential)
    assert "space-a" not in cursor and "owner-a" not in cursor, "cursors must be opaque"

    lab.write(lab.a, "DELTATOKEN", approve=True)
    healthy = svc.read_changelog(lab.a.credential, cursor)
    assert healthy.status == "ok"
    assert [delta.lifecycle for delta in healthy.deltas] == ["asserted"]

    svc.revoke_grant("owner-a", "space-a", "proj-a", Action.READ)
    stale = svc.read_changelog(lab.a.credential, cursor)

    assert stale.status == "resnapshot_required"
    assert stale.deltas == ()
    assert leaks_in(stale) == 0


def test_n10_cursor_is_bound_to_its_principal(svc, lab: Lab) -> None:
    foreign_writes(svc, lab)
    cursor_for_b = svc.changelog_cursor(lab.b.credential)

    stolen = svc.read_changelog(lab.a.credential, cursor_for_b)

    assert stolen.status == "resnapshot_required"
    assert stolen.deltas == ()
    assert leaks_in(stolen) == 0


# --------------------------------------------------------------------------------------
# N11 - three clients: identical receipt semantics, isolated idempotency
# --------------------------------------------------------------------------------------


def test_n11_three_clients_share_receipt_semantics_and_isolated_idempotency(
    svc, lab: Lab
) -> None:
    """Contract-level part of N11; real Kimi/Codex/Grok processes are P2-03."""
    clients = [lab.a, lab.b, lab.c]
    for client in clients:
        lab.write(client, f"from {client.key_id}", approve=True)

    first_round = [
        svc.append_event(
            client.credential, lab.submission(client, "n11-ce", f"from {client.key_id}")
        )
        for client in clients
    ]

    assert len({receipt.server_seq for receipt in first_round}) == 3
    assert len({receipt.contract_version for receipt in first_round}) == 1
    assert len({tuple(sorted(receipt.to_wire())) for receipt in first_round}) == 1
    assert {receipt.source_app for receipt in first_round} == {"codex", "kimi-code", "grok"}

    replays = [
        svc.append_event(
            client.credential, lab.submission(client, "n11-ce", f"from {client.key_id}")
        )
        for client in clients
    ]
    assert [replay.server_seq for replay in replays] == [
        receipt.server_seq for receipt in first_round
    ]
    assert all(replay.replayed for replay in replays)
    assert svc.stats()["events"] == 3

    for client in clients:
        result = svc.read_face(client.credential, Face.SEARCH, FaceRequest(query="from"))
        assert {item.content for item in result.items} == {f"from {client.key_id}"}
