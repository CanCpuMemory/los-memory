"""Migration configuration for Approval system.

This module provides configuration management for the migration
from los-memory to VPS Agent Web.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MigrationPhase(Enum):
    """Migration phases for the approval system."""

    LOCAL_ONLY = "local-only"  # Phase 1: los-memory only
    DUAL_WRITE = "dual-write"  # Phase 2: Write to both systems
    REMOTE_ONLY = "remote-only"  # Phase 3: VPS Agent Web only
    REMOVED = "removed"  # Phase 4: Feature removed


class DualWriteMode(Enum):
    """Dual-write operation modes."""

    STRICT = "strict"  # Both must succeed
    LOCAL_PREFERRED = "local_preferred"  # Local succeeds = success
    REMOTE_PREFERRED = "remote_preferred"  # Remote succeeds = success
    READ_ONLY = "read_only"  # No writes, proxy only


@dataclass
class HMACConfig:
    """HMAC configuration for migration period."""

    # los-memory legacy keys
    legacy_active_secret: Optional[str] = None
    legacy_previous_secret: Optional[str] = None

    # VPS Agent Web keys (for re-signing)
    vps_active_secret: Optional[str] = None
    vps_key_id: str = "v1"

    # Rotation window (24 hours default)
    rotation_window_seconds: int = 24 * 60 * 60

    def __post_init__(self):
        """Load from environment if not provided."""
        if self.legacy_active_secret is None:
            self.legacy_active_secret = os.environ.get("APPROVAL_HMAC_SECRET")
        if self.legacy_previous_secret is None:
            self.legacy_previous_secret = os.environ.get(
                "APPROVAL_HMAC_PREVIOUS_SECRET"
            )
        if self.vps_active_secret is None:
            self.vps_active_secret = os.environ.get("VPS_AGENT_HMAC_SECRET")
        if vps_key_id := os.environ.get("VPS_AGENT_KEY_ID"):
            self.vps_key_id = vps_key_id


@dataclass
class VPSAgentWebConfig:
    """VPS Agent Web connection configuration."""

    url: str = ""
    timeout_seconds: int = 30
    retry_count: int = 3
    health_check_interval_seconds: int = 60

    def __post_init__(self):
        """Load from environment if not provided."""
        if not self.url:
            self.url = os.environ.get(
                "VPS_AGENT_WEB_URL", "https://vps-agent-web.example.com"
            )
        if timeout := os.environ.get("APPROVAL_MIGRATION_TIMEOUT"):
            self.timeout_seconds = int(timeout)


@dataclass
class SSEProxyConfig:
    """SSE event proxy configuration."""

    enabled: bool = True
    buffer_size: int = 1000
    reconnect_timeout_seconds: int = 30
    history_minutes: int = 5


@dataclass
class DualWriteConfig:
    """Dual-write configuration."""

    mode: DualWriteMode = DualWriteMode.STRICT
    sync_interval_seconds: int = 300  # 5 minutes
    conflict_resolution: str = "remote-wins"  # remote-wins, local-wins, manual

    def __post_init__(self):
        """Load mode from environment if specified."""
        mode_str = os.environ.get("APPROVAL_MIGRATION_MODE", "strict")
        try:
            self.mode = DualWriteMode(mode_str)
        except ValueError:
            self.mode = DualWriteMode.STRICT


@dataclass
class MigrationConfig:
    """Complete migration configuration."""

    phase: MigrationPhase = MigrationPhase.LOCAL_ONLY
    hmac: HMACConfig = field(default_factory=HMACConfig)
    vps_agent_web: VPSAgentWebConfig = field(default_factory=VPSAgentWebConfig)
    sse: SSEProxyConfig = field(default_factory=SSEProxyConfig)
    dual_write: DualWriteConfig = field(default_factory=DualWriteConfig)

    # Feature flags
    enable_local: bool = True
    enable_remote: bool = False
    deprecation_warnings: bool = True

    def __post_init__(self):
        """Load configuration from environment."""
        # Determine phase from environment
        phase_str = os.environ.get("APPROVAL_MIGRATION_PHASE", "local-only")
        try:
            self.phase = MigrationPhase(phase_str)
        except ValueError:
            self.phase = MigrationPhase.LOCAL_ONLY

        # Feature flags based on phase
        if self.phase == MigrationPhase.LOCAL_ONLY:
            self.enable_local = True
            self.enable_remote = False
        elif self.phase == MigrationPhase.DUAL_WRITE:
            self.enable_local = True
            self.enable_remote = True
        elif self.phase == MigrationPhase.REMOTE_ONLY:
            self.enable_local = False
            self.enable_remote = True
        elif self.phase == MigrationPhase.REMOVED:
            self.enable_local = False
            self.enable_remote = False

        # Override with explicit flags if set
        if (local_flag := os.environ.get("APPROVAL_ENABLE_LOCAL")) is not None:
            self.enable_local = local_flag.lower() in ("1", "true", "yes")
        if (remote_flag := os.environ.get("APPROVAL_ENABLE_REMOTE")) is not None:
            self.enable_remote = remote_flag.lower() in ("1", "true", "yes")

        # Silence warnings option
        if os.environ.get("MEMORY_APPROVAL_SILENCE_WARNING", "0") == "1":
            self.deprecation_warnings = False

    def is_bridge_enabled(self) -> bool:
        """Check if bridge mode is enabled."""
        return self.phase in (MigrationPhase.DUAL_WRITE, MigrationPhase.REMOTE_ONLY)

    def get_effective_mode(self) -> str:
        """Get effective operation mode description."""
        if self.phase == MigrationPhase.LOCAL_ONLY:
            return "local-only"
        elif self.phase == MigrationPhase.DUAL_WRITE:
            return f"dual-write ({self.dual_write.mode.value})"
        elif self.phase == MigrationPhase.REMOTE_ONLY:
            return "remote-only"
        else:
            return "removed"

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if self.phase in (MigrationPhase.DUAL_WRITE, MigrationPhase.REMOTE_ONLY):
            if not self.vps_agent_web.url:
                errors.append("VPS_AGENT_WEB_URL is required for remote operations")
            if not self.hmac.vps_active_secret:
                errors.append("VPS_AGENT_HMAC_SECRET is required for remote operations")

        if self.phase == MigrationPhase.DUAL_WRITE:
            if not self.hmac.legacy_active_secret:
                errors.append(
                    "APPROVAL_HMAC_SECRET is required for dual-write mode"
                )

        return errors
