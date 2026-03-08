"""SSE Event Stream Proxy for Approval system migration.

This package provides a full SSE (Server-Sent Events) proxy for bridging
local and remote event streams during the migration to VPS Agent Web.

Main Components:
    - SSEProxy: Main coordinator for event streaming
    - EventBufferManager: Thread-safe event buffering with replay support
    - SSEConnectionManager: Persistent connection with auto-reconnection
    - EventTransformer: Event format transformation between local/remote

Example:
    from memory_tool.migrate_out.approval.sse_proxy import SSEProxy

    proxy = SSEProxy(config, vps_client)
    proxy.start()

    for event in proxy.subscribe(last_event_id="..."):
        yield event

    proxy.stop()
"""
from __future__ import annotations

from .buffer import EventBufferManager
from .connection import SSEConnectionError, SSEConnectionManager
from .proxy import SSEProxy
from .transform import EventTransformer

__all__ = [
    "SSEProxy",
    "EventBufferManager",
    "SSEConnectionManager",
    "SSEConnectionError",
    "EventTransformer",
]
