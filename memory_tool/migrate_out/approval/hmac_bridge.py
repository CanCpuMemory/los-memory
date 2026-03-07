"""HMAC bridge for signature verification and re-signing.

This module provides HMAC signature compatibility between los-memory
and VPS Agent Web during migration.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from .config import HMACConfig

import base64
import hashlib
import hmac
import time

from memory_tool.approval_security import (
    HMACValidator,
    MAX_AGE,
    MAX_CLOCK_SKEW,
    MemoryNonceStore,
    NonceStore,
    NONCE_TTL,
    generate_hmac_headers,
)


class HMACVerificationError(Exception):
    """HMAC verification failed."""

    pass


class HMACBridge:
    """Bridge for HMAC signature verification and re-signing.

    This class handles:
    1. Verifying HMAC signatures with los-memory (legacy) keys
    2. Re-signing requests with VPS Agent Web keys for forwarding
    3. Supporting dual-key rotation during migration
    4. Nonce replay attack prevention

    Security Note:
        The bridge uses a NonceStore to prevent replay attacks. Each nonce
        can only be used once within the 5-minute validity window.

    Example:
        config = HMACConfig(
            legacy_active_secret="legacy-secret",
            vps_active_secret="vps-secret"
        )
        bridge = HMACBridge(config)

        # Verify locally (checks nonce uniqueness)
        if bridge.verify_local(headers, payload):
            # Re-sign for remote
            remote_headers = bridge.resign_for_remote(headers, payload)
    """

    def __init__(
        self,
        config: "HMACConfig",
        nonce_store: Optional[NonceStore] = None,
    ):
        self.config = config
        self._nonce_store = nonce_store or MemoryNonceStore()
        self._legacy_validator: Optional[HMACValidator] = None
        self._vps_validator: Optional[HMACValidator] = None

        if config.legacy_active_secret:
            from memory_tool.approval_security import HMACConfig as LegacyHMACConfig

            legacy_config = LegacyHMACConfig(
                active_secret=config.legacy_active_secret,
                previous_secret=config.legacy_previous_secret,
                key_id="v1",
            )
            self._legacy_validator = HMACValidator(
                legacy_config, nonce_store=self._nonce_store
            )

        if config.vps_active_secret:
            from memory_tool.approval_security import HMACConfig as VPSHMACConfig

            vps_config = VPSHMACConfig(
                active_secret=config.vps_active_secret, key_id=config.vps_key_id
            )
            self._vps_validator = HMACValidator(vps_config)

    def verify_local(
        self, headers: Dict[str, str], payload: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Verify HMAC signature with los-memory (legacy) keys.

        Performs the following security checks:
        1. Validates timestamp (not in future, not too old)
        2. Checks nonce uniqueness (prevents replay attacks)
        3. Verifies HMAC signature

        Args:
            headers: Dictionary containing X-Signature, X-Timestamp, X-Nonce
            payload: Optional payload dictionary for verification

        Returns:
            True if signature is valid

        Raises:
            HMACVerificationError: If verification fails with specific reason
        """
        if not self._legacy_validator:
            raise HMACVerificationError("Legacy HMAC not configured")

        # Extract values from headers (case-insensitive lookup)
        signature = self._get_header(headers, "X-Signature", "")
        timestamp_str = self._get_header(headers, "X-Timestamp", "0")
        nonce = self._get_header(headers, "X-Nonce", "")

        if not nonce:
            raise HMACVerificationError("Missing X-Nonce header")

        try:
            timestamp = int(timestamp_str)
        except (TypeError, ValueError):
            raise HMACVerificationError("Invalid X-Timestamp header")

        # Validate timestamp bounds
        now = int(time.time())
        if timestamp > now + MAX_CLOCK_SKEW:
            raise HMACVerificationError(
                f"Timestamp {timestamp}s is in the future (server time: {now})"
            )
        if now - timestamp > MAX_AGE:
            raise HMACVerificationError(
                f"Timestamp {timestamp}s is too old (max age: {MAX_AGE}s)"
            )

        # Check for nonce replay attack
        # The nonce store checks both existence and TTL
        if not self._nonce_store.add(nonce, ttl=NONCE_TTL):
            raise HMACVerificationError(
                "Nonce has already been used (replay attack detected)"
            )

        # Build payload if not provided
        if payload is None:
            payload = self._extract_payload_from_context(headers)

        # Verify signature
        result = self._legacy_validator.verify(
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            payload=payload,
        )

        if not result["valid"]:
            # Remove nonce from store on verification failure
            # so the request can be retried with corrected signature
            # Note: MemoryNonceStore doesn't support removal, but that's okay
            # because the nonce will expire after TTL anyway
            raise HMACVerificationError(
                f"HMAC verification failed: {result.get('error', 'unknown')}"
            )

        return True

    def _get_header(self, headers: Dict[str, str], name: str, default: str = "") -> str:
        """Get header value with case-insensitive lookup.

        HTTP headers are case-insensitive per RFC 7230.
        """
        # Try exact match first
        if name in headers:
            return headers[name]

        # Try case-insensitive lookup
        name_lower = name.lower()
        for key, value in headers.items():
            if key.lower() == name_lower:
                return value

        return default

    def resign_for_remote(
        self, headers: Dict[str, str], payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """Re-sign request with VPS Agent Web keys.

        Args:
            headers: Original headers (for extracting context)
            payload: Request payload

        Returns:
            New headers with VPS Agent Web signature
        """
        if not self._vps_validator:
            raise HMACVerificationError("VPS Agent Web HMAC not configured")

        if payload is None:
            payload = self._extract_payload_from_context(headers)

        # Generate new HMAC headers with VPS secret
        return generate_hmac_headers(
            payload=payload,
            secret=self.config.vps_active_secret,
            key_id=self.config.vps_key_id,
        )

    def verify_and_resign(
        self, headers: Dict[str, str], payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """Verify local signature and re-sign for remote.

        Args:
            headers: Original headers with los-memory signature
            payload: Request payload

        Returns:
            New headers with VPS Agent Web signature

        Raises:
            HMACVerificationError: If local verification fails
        """
        self.verify_local(headers, payload)
        return self.resign_for_remote(headers, payload)

    def _extract_payload_from_context(self, headers: Dict[str, str]) -> Dict[str, Any]:
        """Extract payload from request context.

        This is used when payload is not explicitly provided.
        In practice, the payload should be passed explicitly.
        """
        # Return minimal payload structure
        # The actual implementation should extract from request context
        return {
            "job_id": headers.get("X-Job-ID", ""),
            "action": headers.get("X-Action", ""),
            "actor_id": headers.get("X-Actor-ID", ""),
            "version": int(headers.get("X-Version", "0")),
            "reason": headers.get("X-Reason", ""),
        }

    def is_legacy_configured(self) -> bool:
        """Check if legacy (los-memory) HMAC is configured."""
        return self._legacy_validator is not None

    def is_vps_configured(self) -> bool:
        """Check if VPS Agent Web HMAC is configured."""
        return self._vps_validator is not None

    def generate_local_signature(
        self, payload: Dict[str, Any]
    ) -> Dict[str, str]:
        """Generate HMAC signature with los-memory keys.

        This is useful for testing and for clients that need to
        generate signatures before sending requests.
        """
        if not self._legacy_validator or not self.config.legacy_active_secret:
            raise HMACVerificationError("Legacy HMAC not configured")

        return generate_hmac_headers(
            payload=payload,
            secret=self.config.legacy_active_secret,
            key_id="v1",
        )
