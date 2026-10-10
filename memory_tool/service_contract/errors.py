"""Typed refusals for the P2-01 service contract layer.

Every refusal carries a stable machine-readable ``code``.  Clients branch on the code, never
on the human-readable message, and every refusal is safe to serialize back to the caller:
details never include credentials and never include content the caller may not read.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def _jsonable(value: Any) -> Any:
    """Coerce an arbitrary detail value into something JSON-serializable."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    to_wire = getattr(value, "to_wire", None)
    if callable(to_wire):
        return _jsonable(to_wire())
    return repr(value)


class ContractError(Exception):
    """Base class for contract refusals.

    Attributes:
        code: Stable snake_case identifier, part of the wire contract.
        http_status: Status a transport adapter should map this refusal to.
        message: Human-readable explanation (not part of the contract).
        details: JSON-serializable context for the caller.
    """

    code = "contract_error"
    http_status = 400

    def __init__(self, message: str, **details: Any) -> None:
        """Record a refusal with its stable code and JSON-safe details."""
        super().__init__(message)
        self.message = message
        self.details: Dict[str, Any] = {key: _jsonable(value) for key, value in details.items()}

    def to_wire(self) -> Dict[str, Any]:
        """Render the refusal in the versioned error envelope."""
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "details": self.details},
        }

    def __str__(self) -> str:
        """Render the refusal for humans; clients must branch on ``code``."""
        if not self.details:
            return self.message
        return f"{self.message} ({self.details})"


class MissingContractVersion(ContractError):
    """The request did not declare ``contract_version``."""

    code = "missing_contract_version"


class UnsupportedContractVersion(ContractError):
    """The request declared a contract version this server cannot serve."""

    code = "unsupported_contract_version"
    http_status = 409


class IdentityFieldRejected(ContractError):
    """The payload tried to set a field that only the server may compute."""

    code = "identity_field_rejected"


class Unauthenticated(ContractError):
    """The credential is unknown, revoked, or wrong."""

    code = "unauthenticated"
    http_status = 401


class FaceNotGranted(ContractError):
    """The principal holds no grant for the action a read face requires."""

    code = "face_not_granted"
    http_status = 403


class ScopeNotGranted(ContractError):
    """The request named a scope outside the principal's grants."""

    code = "scope_not_granted"
    http_status = 403


class RecordNotFound(ContractError):
    """The requested record does not exist inside the caller's scope."""

    code = "record_not_found"
    http_status = 404


class RetractedRecordNotReadable(ContractError):
    """The record was retracted, so canonical reads are blocked."""

    code = "retracted_record_not_readable"
    http_status = 410


class ErasedRecordNotReadable(ContractError):
    """The record was erased, so it is unreachable even by id."""

    code = "erased_record_not_readable"
    http_status = 410


class IdempotencyConflict(ContractError):
    """The same idempotency key arrived with a different payload."""

    code = "idempotency_conflict"
    http_status = 409

    def __init__(self, message: str, stored_receipt: Any = None, **details: Any) -> None:
        """Carry the receipt that already owns this idempotency key."""
        super().__init__(message, **details)
        self.stored_receipt = stored_receipt


class RevisionConflict(ContractError):
    """A revision edit was based on a revision that is no longer current."""

    code = "revision_conflict"
    http_status = 409

    def __init__(
        self, message: str, current_revision: Optional[str] = None, **details: Any
    ) -> None:
        """Carry the revision that is current, so the caller can re-read and retry."""
        super().__init__(message, **details)
        self.current_revision = current_revision


class EvidenceRequiredForAssertion(ContractError):
    """``asserted`` may only be reached through evidence (invariant: no evidence-free approval)."""

    code = "evidence_required_for_assertion"
    http_status = 422


class OfflineWriteNotAccepted(ContractError):
    """A client tried to present an offline write as accepted."""

    code = "offline_write_not_accepted"
    http_status = 409


class WireSchemaViolation(ContractError):
    """A payload did not conform to its versioned wire schema."""

    code = "wire_schema_violation"


class UnsupportedSchemaKeyword(ContractError):
    """A schema used a validation keyword this contract does not implement."""

    code = "unsupported_schema_keyword"


__all__ = [
    "ContractError",
    "ErasedRecordNotReadable",
    "EvidenceRequiredForAssertion",
    "FaceNotGranted",
    "IdempotencyConflict",
    "IdentityFieldRejected",
    "MissingContractVersion",
    "OfflineWriteNotAccepted",
    "RecordNotFound",
    "RetractedRecordNotReadable",
    "RevisionConflict",
    "ScopeNotGranted",
    "Unauthenticated",
    "UnsupportedContractVersion",
    "UnsupportedSchemaKeyword",
    "WireSchemaViolation",
]
