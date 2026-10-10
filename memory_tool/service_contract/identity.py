"""Identity and authorization model (invariant I4).

``owner_id`` / ``space_id`` / ``visibility`` / ``device_id`` / ``actor_agent_id`` are computed by
the server from the presented credential.  A client never submits them; a tool request only says
which scopes it *wants* to touch, and the effective scope is the intersection with the grants.
Credentials are stored as a hash plus a revocable key id, never in plaintext.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from .errors import FaceNotGranted, IdentityFieldRejected, ScopeNotGranted, Unauthenticated


class Action(str, Enum):
    """A grantable operation.  The tuple ``(owner, space, project, action)`` is one grant."""

    READ = "read"
    EVENT_APPEND = "event.append"
    MEMORY_PROPOSE = "memory.propose"
    MEMORY_REVISE = "memory.revise"
    MEMORY_RETRACT = "memory.retract"
    MEMORY_ERASE = "memory.erase"
    CONFLICT_RESOLVE = "conflict.resolve"
    HISTORY = "history"
    EXPORT = "export"
    CHANGELOG_READ = "changelog.read"


class Face(str, Enum):
    """A read surface.  Each face is enumerated so isolation is counted per face, not in bulk."""

    SEARCH = "search"
    GET = "get"
    GRAPH = "graph"
    CACHE = "cache"
    BUNDLE = "bundle"
    EXPORT = "export"
    HISTORY = "history"


#: The action each read face requires.  Reading a project is not the same grant as exporting it.
FACE_ACTION: Dict[Face, Action] = {
    Face.SEARCH: Action.READ,
    Face.GET: Action.READ,
    Face.GRAPH: Action.READ,
    Face.CACHE: Action.READ,
    Face.BUNDLE: Action.READ,
    Face.EXPORT: Action.EXPORT,
    Face.HISTORY: Action.HISTORY,
}


class Visibility(str, Enum):
    """Server-derived sharing scope of a memory unit."""

    AGENT_PRIVATE = "agent_private"
    PROJECT_SHARED = "project_shared"
    OWNER_SHARED = "owner_shared"


#: Fields the server computes.  Submitting any of them is a refusal, not something to strip.
IDENTITY_FIELDS: FrozenSet[str] = frozenset(
    {
        "actor_agent_id",
        "device_id",
        "grant",
        "grants",
        "hashed_secret",
        "host_agent_id",
        "key_id",
        "owner",
        "owner_id",
        "permission_epoch",
        "principal",
        "principal_key_id",
        "secret",
        "space",
        "space_id",
        "visibility",
    }
)

#: Fields that only exist after the server has committed something.
SERVER_FIELDS: FrozenSet[str] = frozenset(
    {
        "accepted_at",
        "created_at",
        "current_revision",
        "lifecycle",
        "receipt_id",
        "receipt_state",
        "received_at",
        "recorded_at",
        "server_seq",
        "stored_at",
    }
)

REJECTED_FIELDS: FrozenSet[str] = IDENTITY_FIELDS | SERVER_FIELDS


def scan_rejected_fields(
    payload: Mapping[str, object], prefix: str = ""
) -> Optional[Tuple[str, str]]:
    """Find the first server-owned field in a payload.

    Args:
        payload: The caller-supplied payload.
        prefix: Dotted path prefix used for nested reporting.

    Returns:
        ``(dotted_path, reason)`` for the first offending field, else ``None``.
    """
    for key, value in payload.items():
        path = f"{prefix}{key}"
        if key in REJECTED_FIELDS:
            return path, "server_owned"
        if isinstance(value, Mapping):
            found = scan_rejected_fields(value, prefix=f"{path}.")
            if found is not None:
                return found
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    found = scan_rejected_fields(item, prefix=f"{path}[{index}].")
                    if found is not None:
                        return found
    return None


def reject_server_owned_fields(payload: Mapping[str, object]) -> None:
    """Refuse a payload that sets a server-owned field.

    Args:
        payload: The caller-supplied payload.

    Raises:
        IdentityFieldRejected: If a server-owned field is present.
    """
    found = scan_rejected_fields(payload)
    if found is not None:
        field, reason = found
        raise IdentityFieldRejected(
            "identity and receipt fields are computed by the server and cannot be submitted",
            field=field,
            reason=reason,
        )


def hash_secret(key_id: str, secret: str) -> str:
    """Hash a bearer credential for storage.

    The secret is 256 bits of entropy, so a plain salted digest is not brute-forceable and the
    server never needs the plaintext again.

    Args:
        key_id: The key the secret belongs to.
        secret: The client-held secret.

    Returns:
        Hex digest stored server-side.
    """
    return hashlib.sha256(f"{key_id}:{secret}".encode("utf-8")).hexdigest()


def verify_secret(principal: "Principal", secret: str) -> bool:
    """Constant-time check of a presented secret."""
    expected = principal.hashed_secret
    if not expected:
        return False
    return hmac.compare_digest(expected, hash_secret(principal.key_id, secret))


def utc_now_iso() -> str:
    """Return the current UTC time in the repo's ``...Z`` format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_instant(value: str) -> Optional[float]:
    """Parse an ISO-8601 instant into epoch seconds, or ``None`` when unparsable."""
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class Credential:
    """What a client presents.  Secret material never reaches the store."""

    key_id: str
    secret: str


@dataclass(frozen=True)
class Principal:
    """A registered device credential.  ``owner_id`` is derived here, never submitted."""

    key_id: str
    owner_id: str
    device_id: str
    hashed_secret: str
    actor_agent_id: str = "unknown"
    created_at: str = ""
    revoked_at: Optional[str] = None


@dataclass(frozen=True)
class Registration:
    """The one-time result of registering a device.  The secret is returned once, not stored."""

    key_id: str
    secret: str
    owner_id: str
    device_id: str
    actor_agent_id: str = "unknown"

    @property
    def credential(self) -> Credential:
        """The credential this registration authorizes."""
        return Credential(key_id=self.key_id, secret=self.secret)

    def to_wire(self) -> Dict[str, object]:
        """Render the registration without the secret (for logging/receipts)."""
        return {
            "key_id": self.key_id,
            "owner_id": self.owner_id,
            "device_id": self.device_id,
            "actor_agent_id": self.actor_agent_id,
            "secret_returned_once": True,
        }


@dataclass(frozen=True)
class Grant:
    """One whitelist entry: ``(owner, space, project, action)`` with optional expiry/revocation."""

    owner_id: str
    space_id: str
    project_id: str
    action: Action
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None

    def is_active(self, now: str) -> bool:
        """Return whether this grant applies at ``now``."""
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        now_epoch = _parse_instant(now)
        expiry_epoch = _parse_instant(self.expires_at)
        if now_epoch is None or expiry_epoch is None:
            return False
        return now_epoch < expiry_epoch


@dataclass(frozen=True)
class RequestedScope:
    """What a caller asks for.  It expresses desire, never authority."""

    projects: Tuple[str, ...] = ()
    spaces: Tuple[str, ...] = ()
    owner_shared: bool = False

    def is_explicit(self) -> bool:
        """Return whether the caller named scope items explicitly."""
        return bool(self.projects or self.spaces)

    def to_wire(self) -> Dict[str, object]:
        """Render the request scope for the wire."""
        return {
            "projects": list(self.projects),
            "spaces": list(self.spaces),
            "owner_shared": self.owner_shared,
        }

    @classmethod
    def from_wire(cls, payload: Optional[Mapping[str, object]]) -> "RequestedScope":
        """Parse a request scope from a wire payload."""
        if payload is None:
            return cls()
        return cls(
            projects=tuple(str(item) for item in payload.get("projects", ()) or ()),
            spaces=tuple(str(item) for item in payload.get("spaces", ()) or ()),
            owner_shared=bool(payload.get("owner_shared", False)),
        )


@dataclass(frozen=True)
class EffectiveScope:
    """The intersection of what was requested with what was granted.

    ``denied`` names everything that was asked for and dropped, so narrowing is never silent.
    """

    owner_id: str
    spaces: Tuple[str, ...] = ()
    projects: Tuple[str, ...] = ()
    actions: FrozenSet[Action] = field(default_factory=frozenset)
    denied: Tuple[str, ...] = ()

    def covers(self, space_id: str, project_id: str) -> bool:
        """Return whether this scope covers one space/project pair."""
        return space_id in self.spaces and project_id in self.projects

    def to_wire(self) -> Dict[str, object]:
        """Render the effective scope for a receipt or result envelope."""
        return {
            "owner_id": self.owner_id,
            "spaces": list(self.spaces),
            "projects": list(self.projects),
            "actions": sorted(action.value for action in self.actions),
            "denied": list(self.denied),
        }


def _grant_pairs(grants: Iterable[Grant]) -> List[Tuple[str, str]]:
    """Return the distinct ``(space, project)`` pairs covered by a set of grants."""
    return sorted({(grant.space_id, grant.project_id) for grant in grants})


def compute_effective_scope(
    principal: Principal,
    grants: Sequence[Grant],
    requested: RequestedScope,
    now: str,
    required_action: Optional[Action] = None,
) -> EffectiveScope:
    """Compute ``requested ∩ granted`` for one principal.

    Args:
        principal: The authenticated principal.
        grants: Every grant known to the store.
        requested: What the caller asked for.
        now: Current instant, used for expiry checks.
        required_action: The action this request performs, if any.

    Returns:
        The effective scope, with every dropped request item named in ``denied``.

    Raises:
        ScopeNotGranted: If the caller named scope items and none of them survived.
    """
    mine = [
        grant
        for grant in grants
        if grant.owner_id == principal.owner_id and grant.is_active(now)
    ]
    actions = frozenset(grant.action for grant in mine)
    if required_action is None:
        authorised = mine
    else:
        authorised = [grant for grant in mine if grant.action is required_action]
        if not authorised:
            raise FaceNotGranted(
                "the principal holds no grant for the action this request needs",
                action=required_action.value,
                owner_id=principal.owner_id,
            )

    pairs = _grant_pairs(authorised)
    if requested.is_explicit():
        allowed = [
            (space, project)
            for space, project in pairs
            if (not requested.spaces or space in requested.spaces)
            and (not requested.projects or project in requested.projects)
        ]
        allowed_spaces = {space for space, _ in allowed}
        allowed_projects = {project for _, project in allowed}
        denied = tuple(
            sorted(
                {item for item in requested.spaces if item not in allowed_spaces}
                | {item for item in requested.projects if item not in allowed_projects}
            )
        )
        if not allowed:
            raise ScopeNotGranted(
                "the requested scope is outside the principal's grants",
                requested=sorted(list(requested.spaces) + list(requested.projects)),
                granted_spaces=sorted({space for space, _ in pairs}),
                granted_projects=sorted({project for _, project in pairs}),
            )
    else:
        allowed = pairs
        denied = ()

    return EffectiveScope(
        owner_id=principal.owner_id,
        spaces=tuple(sorted({space for space, _ in allowed})),
        projects=tuple(sorted({project for _, project in allowed})),
        actions=actions,
        denied=denied,
    )


class CredentialRegistry:
    """The single source of truth for device credentials (design §7.2)."""

    def __init__(self) -> None:
        """Create an empty registry; the store is the only source of truth."""
        self._principals: Dict[str, Principal] = {}

    def register(
        self,
        owner_id: str,
        device_id: str,
        *,
        key_id: Optional[str] = None,
        actor_agent_id: str = "unknown",
        now: str = "",
    ) -> Registration:
        """Register a device credential; the secret is generated here and returned once."""
        resolved_key_id = key_id or f"key-{secrets.token_hex(6)}"
        secret = secrets.token_urlsafe(32)
        self._principals[resolved_key_id] = Principal(
            key_id=resolved_key_id,
            owner_id=owner_id,
            device_id=device_id,
            actor_agent_id=actor_agent_id,
            hashed_secret=hash_secret(resolved_key_id, secret),
            created_at=now or utc_now_iso(),
        )
        return Registration(
            key_id=resolved_key_id,
            secret=secret,
            owner_id=owner_id,
            device_id=device_id,
            actor_agent_id=actor_agent_id,
        )

    def get(self, key_id: str) -> Optional[Principal]:
        """Return a principal by key id."""
        return self._principals.get(key_id)

    def all(self) -> Tuple[Principal, ...]:
        """Return every principal (hashes only)."""
        return tuple(self._principals.values())

    def revoke(self, key_id: str, now: str = "") -> None:
        """Revoke a credential without deleting its history."""
        principal = self._principals.get(key_id)
        if principal is None:
            return
        self._principals[key_id] = replace(principal, revoked_at=now or utc_now_iso())

    def authenticate(self, credential: Credential, now: str = "") -> Principal:
        """Authenticate a credential.

        Args:
            credential: The presented key id and secret.
            now: Current instant.

        Returns:
            The active principal.

        Raises:
            Unauthenticated: If the key is unknown, revoked, or the secret is wrong.  The refusal
                is identical in all three cases so it cannot be used to probe for key ids.
        """
        principal = self._principals.get(credential.key_id)
        if (
            principal is None
            or principal.revoked_at is not None
            or not verify_secret(principal, credential.secret)
        ):
            raise Unauthenticated("credential is not valid")
        return principal

    def snapshot(self) -> List[Principal]:
        """Return principals for a state snapshot (hashes only, never secrets)."""
        return list(self._principals.values())

    def restore(self, principals: Iterable[Principal]) -> None:
        """Restore principals from a snapshot."""
        self._principals = {principal.key_id: principal for principal in principals}


__all__ = [
    "FACE_ACTION",
    "IDENTITY_FIELDS",
    "REJECTED_FIELDS",
    "SERVER_FIELDS",
    "Action",
    "Credential",
    "CredentialRegistry",
    "EffectiveScope",
    "Face",
    "Grant",
    "Principal",
    "Registration",
    "RequestedScope",
    "Visibility",
    "compute_effective_scope",
    "hash_secret",
    "reject_server_owned_fields",
    "scan_rejected_fields",
    "utc_now_iso",
    "verify_secret",
]
