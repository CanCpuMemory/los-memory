"""Receipt semantics (design §2.2/§2.5).

The whole point of this module is to keep four things apart that are easy to conflate:

* ``accepted`` means *the original payload is durably stored* - not that a long-term fact was
  extracted and not that anything is searchable yet.  Stage flags carry that separately.
* a replay of the same idempotency key returns the **same** receipt (same ``server_seq``);
* a conflict is refused rather than overwritten;
* ``queued`` is the only state a client may ever claim for itself.  ``accepted`` is minted by
  the server, inside the same transaction that wrote the event and the outbox entry.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .errors import OfflineWriteNotAccepted
from .identity import EffectiveScope
from .models import Degradation
from .versioning import CONTRACT_VERSION


class ReceiptState(str, Enum):
    """States a receipt can be in."""

    ACCEPTED = "accepted"
    QUEUED = "queued"


class OutboxState(str, Enum):
    """Client-side outbox states.  There is deliberately no client-side ``accepted``."""

    QUEUED = "queued"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class Stages:
    """Per-stage progress.  ``accepted`` does not imply any of the later stages."""

    canonical_committed: bool = False
    lexical_ready: bool = False
    semantic_ready: bool = False
    graph_ready: bool = False

    def to_wire(self) -> Dict[str, bool]:
        """Render the stages for the wire."""
        return {
            "canonical_committed": self.canonical_committed,
            "lexical_ready": self.lexical_ready,
            "semantic_ready": self.semantic_ready,
            "graph_ready": self.graph_ready,
        }


@dataclass(frozen=True)
class Receipt:
    """The server's answer to a write.  Replays return an identical receipt."""

    receipt_id: str
    state: ReceiptState
    operation: str
    principal_key_id: str
    owner_id: str
    client_event_id: str
    payload_hash: str
    received_at: str
    contract_version: str = CONTRACT_VERSION
    server_seq: Optional[int] = None
    observed_at: Optional[str] = None
    source_app: Optional[str] = None
    replayed: bool = False
    stages: Stages = field(default_factory=Stages)
    degradations: Tuple[Degradation, ...] = ()
    effective_scope: Optional[EffectiveScope] = None
    denied: Tuple[str, ...] = ()
    erasure_pending: Tuple[str, ...] = ()
    derived_pending: Tuple[str, ...] = ()
    resnapshot_required: bool = False
    cursor: Optional[str] = None
    memory_id: Optional[str] = None
    revision_id: Optional[str] = None
    lifecycle: Optional[str] = None

    def to_wire(self) -> Dict[str, Any]:
        """Render the receipt for the wire."""
        return {
            "receipt_id": self.receipt_id,
            "state": self.state.value,
            "operation": self.operation,
            "contract_version": self.contract_version,
            "principal_key_id": self.principal_key_id,
            "owner_id": self.owner_id,
            "client_event_id": self.client_event_id,
            "payload_hash": self.payload_hash,
            "server_seq": self.server_seq,
            "observed_at": self.observed_at,
            "received_at": self.received_at,
            "source_app": self.source_app,
            "replayed": self.replayed,
            "stages": self.stages.to_wire(),
            "degradations": [item.to_wire() for item in self.degradations],
            "effective_scope": self.effective_scope.to_wire() if self.effective_scope else None,
            "denied": list(self.denied),
            "erasure_pending": list(self.erasure_pending),
            "derived_pending": list(self.derived_pending),
            "resnapshot_required": self.resnapshot_required,
            "cursor": self.cursor,
            "memory_id": self.memory_id,
            "revision_id": self.revision_id,
            "lifecycle": self.lifecycle,
        }


def make_queued_receipt(
    *,
    principal_key_id: str,
    owner_id: str,
    client_event_id: str,
    payload_hash: str,
    created_at: str,
    operation: str = "event.append",
) -> Receipt:
    """Build the receipt a client may produce for itself while offline.

    A client-side receipt is always ``queued``.  Minting ``accepted`` locally is refused, because
    it would claim durability the client cannot know about.
    """
    return Receipt(
        receipt_id=f"local-{client_event_id}",
        state=ReceiptState.QUEUED,
        operation=operation,
        principal_key_id=principal_key_id,
        owner_id=owner_id,
        client_event_id=client_event_id,
        payload_hash=payload_hash,
        received_at=created_at,
    )


@dataclass
class ClientOutboxItem:
    """One durable client-side pending write.

    The item survives process interruption: only a server receipt clears it, so a retry after a
    crash re-sends the *same* ``client_event_id`` and the server returns the same receipt.
    """

    submission: Any
    state: OutboxState = OutboxState.QUEUED
    attempts: int = 0
    receipt_server_seq: Optional[int] = None
    conflict: bool = False

    @property
    def client_event_id(self) -> str:
        """The idempotency key this item will re-use on every retry."""
        return str(self.submission.client_event_id)


class ClientOutbox:
    """The client half of the at-least-once protocol."""

    def __init__(self) -> None:
        """Create an empty durable outbox."""
        self._items: "OrderedDict[str, ClientOutboxItem]" = OrderedDict()

    @property
    def items(self) -> List[ClientOutboxItem]:
        """The pending items, in enqueue order."""
        return list(self._items.values())

    def enqueue(self, submission: Any) -> ClientOutboxItem:
        """Add a submission, or return the existing item for the same ``client_event_id``."""
        existing = self._items.get(str(submission.client_event_id))
        if existing is not None:
            return existing
        item = ClientOutboxItem(submission=submission)
        self._items[item.client_event_id] = item
        return item

    def get(self, client_event_id: str) -> ClientOutboxItem:
        """Return one item by idempotency key."""
        return self._items[client_event_id]

    def state_of(self, client_event_id: str) -> OutboxState:
        """Return the state of one item."""
        return self._items[client_event_id].state

    def mark_sent(self, client_event_id: str) -> ClientOutboxItem:
        """Record that a send is in flight."""
        item = self._items[client_event_id]
        item.state = OutboxState.SENT
        item.attempts += 1
        return item

    def mark_conflict(self, client_event_id: str) -> ClientOutboxItem:
        """Park an item that the server refused as a conflict; it is never silently dropped."""
        item = self._items[client_event_id]
        item.state = OutboxState.CONFLICT
        item.conflict = True
        return item

    def acknowledge(self, receipt: Receipt) -> ClientOutboxItem:
        """Clear an item **only** on an accepted (or replayed) server receipt.

        Args:
            receipt: The receipt returned by the server.

        Returns:
            The updated item.

        Raises:
            OfflineWriteNotAccepted: If the receipt is not an accepted server receipt.
        """
        item = self._items[receipt.client_event_id]
        if receipt.state is not ReceiptState.ACCEPTED:
            raise OfflineWriteNotAccepted(
                "an outbox item may only be cleared by an accepted server receipt",
                client_event_id=receipt.client_event_id,
                receipt_state=receipt.state.value,
            )
        item.state = OutboxState.ACKNOWLEDGED
        item.receipt_server_seq = receipt.server_seq
        return item


__all__ = [
    "ClientOutbox",
    "ClientOutboxItem",
    "OutboxState",
    "Receipt",
    "ReceiptState",
    "Stages",
    "make_queued_receipt",
]
