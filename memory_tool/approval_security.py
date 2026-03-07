"""HMAC security for approval callbacks.

This module provides HMAC signature verification and security utilities
for the L2 approval workflow, following P2 Approval Workflow Best Practices.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from .utils import utc_now


# Security constants per P2 approval workflow spec
MAX_CLOCK_SKEW = 60  # 1 minute future tolerance
MAX_AGE = 5 * 60  # 5 minutes max age
NONCE_TTL = 5 * 60  # 5 minutes nonce TTL
KEY_ROTATION_WINDOW = 24 * 60 * 60  # 24 hours dual-key window


@dataclass
class HMACConfig:
    """HMAC configuration for signature verification."""
    active_secret: str
    previous_secret: Optional[str] = None
    key_id: str = "v1"


class NonceStore:
    """Abstract base class for nonce storage."""

    def add(self, nonce: str, ttl: int = NONCE_TTL) -> bool:
        """Add nonce to store. Returns False if nonce already exists."""
        raise NotImplementedError

    def exists(self, nonce: str) -> bool:
        """Check if nonce exists in store."""
        raise NotImplementedError

    def cleanup(self) -> None:
        """Clean up expired nonces."""
        pass


class MemoryNonceStore(NonceStore):
    """In-memory nonce store with TTL.

    Suitable for single-instance deployments.
    DO NOT use in production cluster deployments.
    """

    def __init__(self):
        self._nonces: Dict[str, float] = {}  # nonce -> expiry time
        self._lock = threading.RLock()
        self._last_cleanup = time.time()

    def add(self, nonce: str, ttl: int = NONCE_TTL) -> bool:
        """Add nonce to store. Returns False if nonce already exists."""
        with self._lock:
            now = time.time()

            # Periodic cleanup every 60 seconds
            if now - self._last_cleanup > 60:
                self._cleanup_unlocked(now)
                self._last_cleanup = now

            if nonce in self._nonces:
                return False

            self._nonces[nonce] = now + ttl
            return True

    def exists(self, nonce: str) -> bool:
        """Check if nonce exists in store."""
        with self._lock:
            now = time.time()
            expiry = self._nonces.get(nonce)
            if expiry is None:
                return False
            if now > expiry:
                del self._nonces[nonce]
                return False
            return True

    def _cleanup_unlocked(self, now: float) -> None:
        """Clean up expired nonces (must hold lock)."""
        expired = [n for n, exp in self._nonces.items() if now > exp]
        for n in expired:
            del self._nonces[n]

    def cleanup(self) -> None:
        """Clean up expired nonces."""
        with self._lock:
            self._cleanup_unlocked(time.time())


class SQLiteNonceStore(NonceStore):
    """SQLite-backed nonce store for persistence.

    Suitable for single-instance or small deployments.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Ensure nonce table exists."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_nonces (
                nonce TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_nonce_expires
            ON approval_nonces(expires_at)
        """)
        self.conn.commit()

    def add(self, nonce: str, ttl: int = NONCE_TTL) -> bool:
        """Add nonce to store. Returns False if nonce already exists."""
        from datetime import datetime, timedelta, timezone
        try:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).strftime('%Y-%m-%dT%H:%M:%SZ')
            self.conn.execute(
                "INSERT INTO approval_nonces (nonce, expires_at) VALUES (?, ?)",
                (nonce, expires_at)
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def exists(self, nonce: str) -> bool:
        """Check if nonce exists and is not expired."""
        from datetime import datetime, timezone
        row = self.conn.execute(
            "SELECT expires_at FROM approval_nonces WHERE nonce = ?",
            (nonce,)
        ).fetchone()

        if not row:
            return False

        # Check expiry by parsing both datetimes
        expires_at_str = row["expires_at"]
        expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)

        if now > expires_at:
            self.conn.execute(
                "DELETE FROM approval_nonces WHERE nonce = ?",
                (nonce,)
            )
            self.conn.commit()
            return False

        return True

    def cleanup(self) -> None:
        """Clean up expired nonces."""
        self.conn.execute(
            "DELETE FROM approval_nonces WHERE expires_at < ?",
            (utc_now(),)
        )
        self.conn.commit()


class HMACValidator:
    """HMAC signature validator for approval callbacks.

    Implements security requirements from P2 Approval Workflow:
    - HMAC-SHA256 signature verification
    - Timestamp validation (reject past >5min, future >60s)
    - Nonce replay attack prevention
    - Dual-key rotation support (24h window)

    Example:
        validator = HMACValidator(
            config=HMACConfig(
                active_secret=os.environ["APPROVAL_HMAC_SECRET"],
                previous_secret=os.environ.get("APPROVAL_HMAC_PREV_SECRET"),
                key_id="v1"
            ),
            nonce_store=SQLiteNonceStore(conn)
        )

        # Verify incoming request
        is_valid = validator.verify(
            signature=headers["X-Signature"],
            timestamp=headers["X-Timestamp"],
            nonce=headers["X-Nonce"],
            key_id=headers.get("X-Key-Id", "v1"),
            payload=payload_dict
        )
    """

    def __init__(
        self,
        config: HMACConfig,
        nonce_store: Optional[NonceStore] = None,
    ):
        self.config = config
        self.nonce_store = nonce_store or MemoryNonceStore()

    def generate_signature(
        self,
        payload: Dict[str, Any],
        timestamp: int,
        nonce: str,
        key_id: Optional[str] = None,
    ) -> str:
        """Generate HMAC-SHA256 signature for payload.

        Payload string format: job_id|action|actor_id|timestamp|version|reason
        """
        # Build payload string per contract v1.0
        payload_str = self._build_payload_string(payload, timestamp)

        # Create signature
        secret = self.config.active_secret
        signature = hmac.new(
            secret.encode("utf-8"),
            f"{nonce}:{payload_str}".encode("utf-8"),
            hashlib.sha256
        ).digest()

        return base64.b64encode(signature).decode("utf-8")

    def verify(
        self,
        signature: str,
        timestamp: int,
        nonce: str,
        payload: Dict[str, Any],
        key_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify HMAC signature and security parameters.

        Args:
            signature: Base64-encoded HMAC signature
            timestamp: Unix timestamp from request
            nonce: Unique nonce for replay prevention
            payload: Request payload dictionary
            key_id: Key identifier (optional)

        Returns:
            Dict with "valid" (bool) and "error" (str if invalid)
        """
        # Validate timestamp
        now = int(time.time())

        # Reject future timestamps (>60s)
        if timestamp > now + MAX_CLOCK_SKEW:
            return {
                "valid": False,
                "error": "timestamp_in_future",
                "message": f"Timestamp {timestamp}s ahead of server time",
            }

        # Reject old timestamps (>5min)
        if now - timestamp > MAX_AGE:
            return {
                "valid": False,
                "error": "timestamp_too_old",
                "message": f"Timestamp {timestamp}s older than {MAX_AGE}s",
            }

        # Check nonce for replay attack
        if not self.nonce_store.add(nonce):
            return {
                "valid": False,
                "error": "nonce_reused",
                "message": "Nonce has already been used",
            }

        # Build payload string
        payload_str = self._build_payload_string(payload, timestamp)
        message = f"{nonce}:{payload_str}"

        # Verify signature with active key
        if self._verify_with_secret(signature, message, self.config.active_secret):
            return {"valid": True}

        # Verify with previous key (within rotation window)
        if self.config.previous_secret:
            if self._verify_with_secret(signature, message, self.config.previous_secret):
                return {"valid": True}

        return {
            "valid": False,
            "error": "invalid_signature",
            "message": "HMAC signature verification failed",
        }

    def _build_payload_string(self, payload: Dict[str, Any], timestamp: int) -> str:
        """Build canonical payload string for signing.

        Format: job_id|action|actor_id|timestamp|version|reason
        """
        fields = [
            payload.get("job_id", ""),
            payload.get("action", ""),
            payload.get("actor_id", ""),
            str(timestamp),
            str(payload.get("version", "0")),
            payload.get("reason", ""),
        ]
        return "|".join(fields)

    def _verify_with_secret(self, signature: str, message: str, secret: str) -> bool:
        """Verify signature against message using secret."""
        expected = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).digest()
        expected_b64 = base64.b64encode(expected).decode("utf-8")

        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(signature, expected_b64)


def generate_hmac_headers(
    payload: Dict[str, Any],
    secret: str,
    key_id: str = "v1",
) -> Dict[str, str]:
    """Generate HMAC headers for approval callback.

    Convenience function for clients sending approval requests.

    Args:
        payload: Request payload with job_id, action, actor_id, version, reason
        secret: HMAC secret key
        key_id: Key identifier

    Returns:
        Dict with X-Signature, X-Timestamp, X-Nonce, X-Key-Id headers
    """
    import uuid

    timestamp = int(time.time())
    nonce = str(uuid.uuid4())

    # Create temporary validator for signing
    config = HMACConfig(active_secret=secret, key_id=key_id)
    validator = HMACValidator(config)

    signature = validator.generate_signature(
        payload=payload,
        timestamp=timestamp,
        nonce=nonce,
        key_id=key_id,
    )

    return {
        "X-Signature": signature,
        "X-Timestamp": str(timestamp),
        "X-Nonce": nonce,
        "X-Key-Id": key_id,
    }
