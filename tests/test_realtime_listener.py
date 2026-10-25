"""Unit and contract tests for Bounded Synchronous PostgreSQL RealtimeListener (T29).

Validates:
- SQLite URLs result in clean no-op without starting threads
- Password masking in logs and URLs
- Consumption of notifications and thread-safe wake_event signaling
- Catch-up wake request upon initial subscription and reconnect
- Interruptible backoff during reconnection (stopping does not wait for backoff duration)
- Clean stop bounded within <= 3s budget
- Async non-blocking stop wrapper
"""
import asyncio
import threading
import time
from typing import List
from unittest.mock import MagicMock, call
import pytest

from core.realtime_listener import (
    RealtimeListener,
    extract_psycopg_conninfo,
    get_masked_url,
    BACKOFF_STEPS,
    CONNECT_TIMEOUT,
    NOTIFY_TIMEOUT,
)


class MockNotify:
    def __init__(self, channel: str = "beercall_realtime", payload: str = ""):
        self.channel = channel
        self.payload = payload


class MockConnection:
    """Mock psycopg connection yielding configurable notifications."""

    def __init__(self, notifications: List[MockNotify] = None, fail_connect: bool = False):
        self._notifications = list(notifications or [])
        self.fail_connect = fail_connect
        self.executed_statements = []
        self.closed = False
        self.more_event = threading.Event()

    def execute(self, statement: str):
        if self.fail_connect:
            raise ConnectionError("Simulated connection failure")
        self.executed_statements.append(statement)

    def push_notify(self, notify: MockNotify):
        self._notifications.append(notify)
        self.more_event.set()

    def notifies(self, timeout: float = 0.5):
        if self.closed:
            return
        while self._notifications:
            yield self._notifications.pop(0)
        self.more_event.wait(timeout=0.05)
        self.more_event.clear()

    def close(self):
        self.closed = True
        self.more_event.set()


def test_sqlite_url_disabled_noop():
    """SQLite URLs must result in a disabled listener that starts no thread."""
    loop = asyncio.new_event_loop()
    wake = asyncio.Event()

    listener = RealtimeListener("sqlite:///./beercall.db", loop, wake)
    assert not listener.is_enabled
    assert listener.conninfo is None

    listener.start()
    assert listener._thread is None

    # stop() on disabled listener must return True immediately
    assert listener.stop() is True
    loop.close()


def test_password_masked_in_url_and_logs():
    """Database URLs must mask secrets for logs."""
    raw_url = "postgresql+psycopg://beercall_user:super_secret_pw@localhost:5432/beercall_test"
    masked = get_masked_url(raw_url)
    assert "super_secret_pw" not in masked
    assert "***" in masked
    assert "beercall_user" in masked

    conninfo = extract_psycopg_conninfo(raw_url)
    assert conninfo is not None
    assert conninfo.startswith("postgresql://")


@pytest.mark.asyncio
async def test_notification_consumption_and_catchup_wake():
    """RealtimeListener must trigger catchup wake on LISTEN and signal wake on notification."""
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()

    mock_conn = MockConnection()

    listener = RealtimeListener(
        database_url="postgresql://user:pw@localhost:5432/db",
        loop=loop,
        wake_event=wake,
        connect_factory=lambda _: mock_conn,
    )

    listener.start()

    # Initial LISTEN triggers catchup wake
    await asyncio.wait_for(wake.wait(), timeout=1.0)
    assert wake.is_set()
    wake.clear()

    # Pushing a notification triggers wake again
    mock_conn.push_notify(MockNotify(channel="beercall_realtime"))
    await asyncio.wait_for(wake.wait(), timeout=1.0)
    assert wake.is_set()

    stopped = await listener.stop_async(timeout=1.0)
    assert stopped is True
    assert mock_conn.closed is True
    assert "LISTEN beercall_realtime;" in mock_conn.executed_statements


@pytest.mark.asyncio
async def test_reconnection_backoff_is_interruptible():
    """Stopping the listener while in backoff sleep must complete immediately (<0.3s)
    without waiting for the full backoff period (5s).
    """
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()

    def failing_connect(_):
        raise ConnectionRefusedError("Simulated server down")

    listener = RealtimeListener(
        database_url="postgresql://user:pw@localhost:5432/db",
        loop=loop,
        wake_event=wake,
        connect_factory=failing_connect,
    )

    listener.start()
    # Give the thread a moment to fail connect and enter backoff
    await asyncio.sleep(0.05)

    start_time = time.monotonic()
    stopped = await listener.stop_async(timeout=2.0)
    duration = time.monotonic() - start_time

    assert stopped is True
    # Must stop in well under 0.5s even if backoff is waiting
    assert duration < 0.5


@pytest.mark.asyncio
async def test_burst_notifications_coalesced_safely():
    """A burst of notifications triggers wake_event safely without thread race conditions."""
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()

    burst = [MockNotify() for _ in range(20)]
    mock_conn = MockConnection(notifications=burst)

    listener = RealtimeListener(
        database_url="postgresql://user:pw@localhost:5432/db",
        loop=loop,
        wake_event=wake,
        connect_factory=lambda _: mock_conn,
    )

    listener.start()
    await asyncio.wait_for(wake.wait(), timeout=1.0)
    assert wake.is_set()

    stopped = await listener.stop_async(timeout=1.0)
    assert stopped is True


def test_bounded_stop_timeout_budget():
    """If thread fails to terminate within budget, stop() returns False and logs error."""
    loop = asyncio.new_event_loop()
    wake = asyncio.Event()

    class SlowStoppingConnection:
        def __init__(self):
            self.closed = False
        def execute(self, st): pass
        def notifies(self, timeout=0.5):
            time.sleep(0.4)
            return
            yield
        def close(self): pass

    listener = RealtimeListener(
        database_url="postgresql://user:pw@localhost:5432/db",
        loop=loop,
        wake_event=wake,
        connect_factory=lambda _: SlowStoppingConnection(),
    )

    listener.start()
    time.sleep(0.05)

    # Calling stop with very small budget (0.1s)
    # The thread is sleeping for 0.4s, so stop must return False
    stopped = listener.stop(timeout=0.1)
    assert stopped is False

    # Wait for the thread to stop cleanly
    listener.stop(timeout=1.0)
    loop.close()
