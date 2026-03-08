"""SSE connection management with automatic reconnection.

This module provides persistent SSE connection management with exponential
backoff reconnection for resilient event streaming from VPS Agent Web.
"""
from __future__ import annotations

import logging
import random
import socket
from typing import TYPE_CHECKING, Dict, Iterator, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from ..config import SSEProxyConfig
    from ..vps_client import VPSAgentWebClient

    from memory_tool.approval_events import ApprovalEvent

logger = logging.getLogger(__name__)


class SSEConnectionError(Exception):
    """SSE connection error.

    Attributes:
        message: Error message
        is_transient: Whether error is retryable
        cause: Original exception
    """

    def __init__(
        self,
        message: str,
        is_transient: bool = True,
        cause: Optional[Exception] = None,
    ):
        self.is_transient = is_transient
        self.cause = cause
        super().__init__(message)


class SSEConnectionManager:
    """Manages SSE connection with automatic reconnection.

    Handles persistent connection to VPS Agent Web SSE endpoint with:
    - Exponential backoff reconnection
    - Connection health monitoring
    - SSE stream parsing
    - Graceful disconnection

    Reconnection Strategy:
        - Base delay: 1 second
        - Exponential: 1s, 2s, 4s, 8s, ... up to max 30s
        - Jitter: ±25% to prevent thundering herd
        - Max attempts: None (persistent connection)

    Example:
        manager = SSEConnectionManager(config, vps_client)

        for event in manager.connect(last_event_id="uuid-123"):
            print(f"Received: {event.event_type}")
            # Connection automatically reconnects on failure
    """

    # Reconnection constants
    BASE_DELAY = 1.0
    MAX_DELAY = 30.0
    JITTER_FACTOR = 0.25
    CONNECT_TIMEOUT = 10.0
    READ_TIMEOUT = 300.0  # 5 minutes for SSE

    def __init__(
        self,
        config: "SSEProxyConfig",
        vps_client: "VPSAgentWebClient",
    ):
        """Initialize connection manager.

        Args:
            config: SSE proxy configuration
            vps_client: VPS Agent Web client
        """
        self.config = config
        self.vps_client = vps_client

        # Connection state
        self._current_event_id: Optional[str] = None
        self._connected = False
        self._connection_error: Optional[Exception] = None
        self._reconnect_count = 0
        self._should_stop = False

    def connect(
        self,
        last_event_id: Optional[str] = None,
    ) -> Iterator["ApprovalEvent"]:
        """Establish SSE connection and yield events.

        Automatically reconnects on transient connection failures.
        Yields events until explicitly disconnected or unrecoverable error.

        Args:
            last_event_id: Last event ID for replay on reconnection

        Yields:
            ApprovalEvent objects from the SSE stream

        Raises:
            SSEConnectionError: On unrecoverable connection failure
        """
        self._current_event_id = last_event_id
        self._should_stop = False

        while not self._should_stop:
            try:
                logger.debug(
                    f"Connecting to SSE stream "
                    f"(last_id={self._current_event_id})"
                )

                for event in self._try_connect():
                    self._connected = True
                    self._reconnect_count = 0
                    self._connection_error = None

                    # Update current event ID for potential reconnection
                    self._current_event_id = event.event_id

                    yield event

            except SSEConnectionError as e:
                self._connected = False
                self._connection_error = e

                if not e.is_transient or self._should_stop:
                    # Non-transient error or stop requested
                    logger.error(f"SSE connection failed: {e}")
                    raise

                # Calculate and apply backoff
                self._reconnect_count += 1
                delay = self._calculate_backoff()

                logger.warning(
                    f"SSE connection lost, reconnecting in {delay:.1f}s "
                    f"(attempt {self._reconnect_count})"
                )

                # Wait before reconnecting
                try:
                    import time

                    time.sleep(delay)
                except InterruptedError:
                    break

    def disconnect(self) -> None:
        """Signal disconnection to break connect() loop."""
        self._should_stop = True
        self._connected = False
        logger.info("SSE disconnect requested")

    def is_connected(self) -> bool:
        """Check if currently connected.

        Returns:
            True if connected and receiving events
        """
        return self._connected

    def get_reconnect_count(self) -> int:
        """Get number of reconnections since start.

        Returns:
            Number of reconnections
        """
        return self._reconnect_count

    def get_last_error(self) -> Optional[Exception]:
        """Get last connection error.

        Returns:
            Last error or None
        """
        return self._connection_error

    def _try_connect(self) -> Iterator["ApprovalEvent"]:
        """Single connection attempt.

        Yields:
            ApprovalEvent from SSE stream

        Raises:
            SSEConnectionError: On connection or parse failure
        """
        url = self.vps_client.get_event_stream_url(self._current_event_id)

        req = Request(
            url,
            headers={
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
            },
        )

        # Set socket timeout for connection phase
        original_timeout = socket.getdefaulttimeout()

        try:
            socket.setdefaulttimeout(self.CONNECT_TIMEOUT)

            with urlopen(req, timeout=self.READ_TIMEOUT) as resp:
                if resp.getcode() != 200:
                    raise SSEConnectionError(
                        f"HTTP {resp.getcode()}",
                        is_transient=resp.getcode() >= 500,
                    )

                # Parse SSE stream
                yield from self._parse_stream(resp)

        except HTTPError as e:
            # Classify HTTP errors
            is_transient = e.code >= 500 or e.code == 429  # Server error or rate limit
            raise SSEConnectionError(
                f"HTTP error: {e.code}",
                is_transient=is_transient,
                cause=e,
            ) from e

        except URLError as e:
            # Network errors are generally transient
            raise SSEConnectionError(
                f"Connection error: {e}",
                is_transient=True,
                cause=e,
            ) from e

        except socket.timeout as e:
            raise SSEConnectionError(
                "Connection timeout",
                is_transient=True,
                cause=e,
            ) from e

        finally:
            socket.setdefaulttimeout(original_timeout)

    def _parse_stream(self, response) -> Iterator["ApprovalEvent"]:
        """Parse SSE stream from HTTP response.

        SSE Format:
            event: approval.pending
            id: uuid-123
            data: {"job_id": "...", ...}
            \n

        Args:
            response: HTTP response object with read() method

        Yields:
            Parsed ApprovalEvent objects

        Raises:
            SSEConnectionError: On unrecoverable parse error
        """
        from .transform import EventTransformer

        buffer = ""
        chunk_size = 4096

        try:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    # Connection closed
                    break

                buffer += chunk.decode("utf-8", errors="replace")

                # Process complete messages
                while "\n\n" in buffer:
                    message, buffer = buffer.split("\n\n", 1)

                    if not message.strip():
                        # Heartbeat/keepalive
                        continue

                    try:
                        parsed = EventTransformer.parse_sse_message(message)
                        if parsed:
                            event = (
                                EventTransformer.transform_remote_to_local(parsed)
                            )
                            yield event
                    except Exception as e:
                        logger.warning(f"Failed to parse SSE message: {e}")
                        # Continue to next message, don't break stream
                        continue

        except Exception as e:
            raise SSEConnectionError(
                f"Stream error: {e}",
                is_transient=True,
                cause=e,
            ) from e

    def _calculate_backoff(self) -> float:
        """Calculate exponential backoff with jitter.

        Returns:
            Delay in seconds before next reconnection attempt
        """
        # Exponential: 1s, 2s, 4s, 8s, ... up to max
        delay = min(
            self.BASE_DELAY * (2 ** self._reconnect_count),
            self.MAX_DELAY,
        )

        # Add jitter (±25%) to prevent thundering herd
        jitter = delay * self.JITTER_FACTOR
        delay = delay + random.uniform(-jitter, jitter)

        return max(0.1, delay)  # Minimum 100ms
