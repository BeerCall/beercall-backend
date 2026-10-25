"""Tests for the Realtime Relay Wakeup Loop and Anti-HoL Quarantine (T30 / CHAL-02 / REV-09).

Acceptance validations:
1. Fast wake: wake_event triggers immediate dispatch without waiting 1.0s.
2. Burst drain: >50 events are drained in consecutive batches without sleep while progress is made.
3. Wake during fetch/dispatch: clearing wake BEFORE fetch ensures notifications during a pass are preserved.
4. Fallback polling: fallback 1.0s polling ensures events are dispatched even without wake_event (SQLite / lost notify).
5. Anti-HoL blocking: 50 failing events are quarantined after 5 consecutive failures, unblocking the 51st healthy event.
6. Quarantine readmission: expired quarantine entries (>300s) are automatically re-admitted without process restart.
7. Quarantine registry saturation: max entries limit enforced, logs warning, unexpired entries preserved.
8. Commit error on mark_events_dispatched: database failure is recorded as failure, not counted as progress.
9. Bounded stop: stop_event terminates loop and listener within <= 0.5s.
10. Monotonic purge: 7-day purge runs at 60s monotonic intervals, not on every micro-pass.
"""
import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock, patch
import pytest

from core.realtime_relay import (
    QuarantineRegistry,
    fetch_events_to_dispatch,
    mark_events_dispatched,
    purge_old_events,
    realtime_relay_loop,
    MAX_QUARANTINE_REGISTRY,
    QUARANTINE_FAILURE_THRESHOLD,
    QUARANTINE_DURATION_SECONDS,
)
from models.realtime_event import RealtimeEvent
from models.squad import Squad
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def isolated_loop_env(db_session, monkeypatch):
    """Ensure relay loop runs against test database sessionmaker."""
    monkeypatch.setattr("core.realtime_relay.SessionLocal", sessionmaker(bind=db_session.get_bind()))
    squad = Squad(id=1, name="Wakeup Loop Squad", invite_code="wake-test")
    db_session.add(squad)
    db_session.commit()


@pytest.mark.asyncio
async def test_fast_wake_via_wake_event(db_session, monkeypatch):
    """Signaling wake_event causes immediate dispatch (<0.2s) instead of waiting for 1.0s polling."""
    stop_event = asyncio.Event()
    wake_event = asyncio.Event()
    broadcast_times = []

    async def mock_broadcast(squad_id, payload):
        broadcast_times.append(time.monotonic())
        stop_event.set()

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", mock_broadcast)

    # Disable listener thread for pure loop testing
    mock_listener = Mock()
    mock_listener.stop_async = AsyncMock(return_value=True)

    loop_task = asyncio.create_task(
        realtime_relay_loop(stop_event=stop_event, wake_event=wake_event, listener=mock_listener)
    )

    # Let the loop execute its initial pass (empty DB) and block waiting for wake
    await asyncio.sleep(0.05)

    ev = RealtimeEvent(squad_id=1, payload={"type": "WAKE_FAST", "msg": "hello"})
    db_session.add(ev)
    db_session.commit()

    start_time = time.monotonic()
    # Signal wake
    wake_event.set()

    await loop_task
    assert len(broadcast_times) == 1
    duration = broadcast_times[0] - start_time
    assert duration < 0.25, f"Dispatch took too long after wake: {duration:.3f}s"

    db_session.refresh(ev)
    assert ev.dispatched_at is not None


@pytest.mark.asyncio
async def test_burst_drain_over_50_events_without_sleep(db_session, monkeypatch):
    """When a batch of 50 events makes progress, the loop immediately continues draining without sleeping."""
    stop_event = asyncio.Event()
    wake_event = asyncio.Event()

    # Create 60 events in DB
    events = [RealtimeEvent(squad_id=1, payload={"index": i}) for i in range(60)]
    db_session.add_all(events)
    db_session.commit()

    broadcast_indices = []
    timestamps = []

    async def mock_broadcast(squad_id, payload):
        broadcast_indices.append(payload["index"])
        timestamps.append(time.monotonic())
        if len(broadcast_indices) >= 60:
            stop_event.set()

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", mock_broadcast)

    mock_listener = Mock()
    mock_listener.stop_async = AsyncMock(return_value=True)

    start_time = time.monotonic()
    await realtime_relay_loop(stop_event=stop_event, wake_event=wake_event, listener=mock_listener)
    elapsed = time.monotonic() - start_time

    assert len(broadcast_indices) == 60

    # Specifically prove there was no sleep between the 50th item (end of batch 1) and 51st item (start of batch 2)
    # If the loop had fallen back to sleep, delta would be >= 1.0s
    inter_batch_gap = timestamps[50] - timestamps[49]
    assert inter_batch_gap < 0.2, f"Loop slept between batches: {inter_batch_gap:.3f}s"

    for ev in events:
        db_session.refresh(ev)
        assert ev.dispatched_at is not None


@pytest.mark.asyncio
async def test_wake_cleared_before_fetch_preserves_concurrent_notification(db_session, monkeypatch):
    """Notifications arriving during fetch/broadcast must not be dropped because wake is cleared before fetch."""
    stop_event = asyncio.Event()
    wake_event = asyncio.Event()

    ev1 = RealtimeEvent(squad_id=1, payload={"msg": "first"})
    db_session.add(ev1)
    db_session.commit()

    broadcast_order = []

    async def mock_broadcast(squad_id, payload):
        msg = payload.get("msg")
        broadcast_order.append(msg)
        if msg == "first":
            # Concurrent event inserted and wake signaled during broadcast of first
            ev2 = RealtimeEvent(squad_id=1, payload={"msg": "second"})
            db_session.add(ev2)
            db_session.commit()
            wake_event.set()
        elif msg == "second":
            stop_event.set()

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", mock_broadcast)

    mock_listener = Mock()
    mock_listener.stop_async = AsyncMock(return_value=True)

    start_time = time.monotonic()
    await realtime_relay_loop(stop_event=stop_event, wake_event=wake_event, listener=mock_listener)
    total_time = time.monotonic() - start_time

    assert broadcast_order == ["first", "second"]
    assert total_time < 0.5, f"Second event was delayed: {total_time:.2f}s"


@pytest.mark.asyncio
async def test_fallback_polling_when_no_wake(db_session, monkeypatch):
    """When wake_event is never signaled (SQLite or lost notification), 1.0s fallback timeout drains the queue."""
    stop_event = asyncio.Event()
    wake_event = asyncio.Event()

    initial_fetch = asyncio.Event()
    fetch_count = 0
    loop = asyncio.get_running_loop()

    def tracked_fetch(excluded_ids):
        nonlocal fetch_count
        events = fetch_events_to_dispatch(excluded_ids)
        fetch_count += 1
        if fetch_count == 1:
            assert events == []
            loop.call_soon_threadsafe(initial_fetch.set)
        return events

    monkeypatch.setattr("core.realtime_relay.fetch_events_to_dispatch", tracked_fetch)

    async def mock_broadcast(squad_id, payload):
        stop_event.set()

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", mock_broadcast)

    mock_listener = Mock()
    mock_listener.stop_async = AsyncMock(return_value=True)

    task = asyncio.create_task(realtime_relay_loop(stop_event, wake_event, mock_listener))
    try:
        await asyncio.wait_for(initial_fetch.wait(), timeout=2.0)
        # Insert AFTER the empty initial scan; no listener and no wake signal.
        ev = RealtimeEvent(squad_id=1, payload={"msg": "fallback_polled"})
        db_session.add(ev)
        db_session.commit()
        await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
    finally:
        stop_event.set()
        await task

    assert fetch_count >= 2
    assert not wake_event.is_set()
    db_session.refresh(ev)
    assert ev.dispatched_at is not None


@pytest.mark.asyncio
async def test_anti_hol_50_poison_events_unblock_51st_healthy_event(db_session, monkeypatch):
    """Anti-HoL: 50 poison events fail 5 times, enter quarantine, and unblock the 51st healthy event."""
    stop_event = asyncio.Event()
    wake_event = asyncio.Event()

    # Seed 50 poison events followed by 1 healthy event
    poison_events = [RealtimeEvent(squad_id=1, payload={"poison": True, "idx": i}) for i in range(50)]
    healthy_event = RealtimeEvent(squad_id=1, payload={"poison": False, "idx": 51})
    db_session.add_all(poison_events + [healthy_event])
    db_session.commit()

    healthy_broadcast = asyncio.Event()
    quarantine = QuarantineRegistry(threshold=5, duration=300.0)

    async def mock_broadcast(squad_id, payload):
        if payload.get("poison"):
            raise RuntimeError("Poison pill delivery crash")
        if payload.get("idx") == 51:
            healthy_broadcast.set()
            stop_event.set()

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", mock_broadcast)

    mock_listener = Mock()
    mock_listener.stop_async = AsyncMock(return_value=True)

    # Run relay loop with fast wake triggers for 5 retry passes
    async def drive_retries():
        # Drive 5 passes to reach the threshold of 5 failures
        for _ in range(6):
            if healthy_broadcast.is_set():
                break
            await asyncio.sleep(0.05)
            wake_event.set()

    loop_task = asyncio.create_task(
        realtime_relay_loop(
            stop_event=stop_event,
            wake_event=wake_event,
            listener=mock_listener,
            quarantine=quarantine,
        )
    )
    driver_task = asyncio.create_task(drive_retries())

    try:
        await asyncio.wait_for(healthy_broadcast.wait(), timeout=3.0)
    finally:
        stop_event.set()
        await asyncio.gather(loop_task, driver_task, return_exceptions=True)

    # 1. Healthy event was broadcast and marked dispatched
    assert healthy_broadcast.is_set()
    db_session.refresh(healthy_event)
    assert healthy_event.dispatched_at is not None

    # 2. Poison events were NOT dispatched and are currently quarantined
    now = time.monotonic()
    quarantined = quarantine.get_quarantined_ids(now)
    assert len(quarantined) == 50
    for p_ev in poison_events:
        db_session.refresh(p_ev)
        assert p_ev.dispatched_at is None
        assert p_ev.id in quarantined


def test_fetch_excludes_every_quarantined_event_beyond_1000(db_session):
    """All 4096 registry slots must be respected, not just the first SQL chunk."""
    created_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    poisoned = [RealtimeEvent(squad_id=1, payload={"poison": True}, created_at=created_at) for _ in range(1050)]
    healthy = RealtimeEvent(squad_id=1, payload={"healthy": True})
    db_session.add_all(poisoned + [healthy])
    db_session.commit()

    events = fetch_events_to_dispatch([event.id for event in poisoned])
    assert [event["id"] for event in events] == [healthy.id]
    assert all(event.dispatched_at is None for event in poisoned)


def test_quarantine_readmission_after_expiration():
    """An event quarantined for 300s is re-admitted once its expiration timestamp passes."""
    registry = QuarantineRegistry(threshold=3, duration=300.0)
    event_id = "test-uuid-readmit"

    t0 = 1000.0
    registry.record_failure(event_id, t0)
    registry.record_failure(event_id, t0 + 1.0)
    registry.record_failure(event_id, t0 + 2.0)

    # After 3 failures, event is quarantined until t0 + 2.0 + 300.0 = 1302.0
    assert registry.is_quarantined(event_id, now=1100.0) is True
    assert event_id in registry.get_quarantined_ids(now=1100.0)

    # Before expiration (1301.9s) -> still quarantined
    assert registry.is_quarantined(event_id, now=1301.9) is True

    # At expiration (1302.1s) -> readmitted automatically
    assert registry.is_quarantined(event_id, now=1302.1) is False
    assert event_id not in registry.get_quarantined_ids(now=1302.1)


def test_quarantine_registry_saturation_and_purge():
    """QuarantineRegistry enforces max_entries capacity and cleanly purges expired items."""
    max_cap = 5
    registry = QuarantineRegistry(max_entries=max_cap, threshold=2, duration=10.0)

    t0 = 100.0
    # Fill registry with 5 items
    for i in range(max_cap):
        registry.record_failure(f"ev_{i}", t0)

    assert len(registry._entries) == 5

    # 6th item should be rejected due to saturation backoff without evicting unexpired items
    registry.record_failure("ev_overflow", t0)
    assert "ev_overflow" not in registry._entries
    assert len(registry._entries) == 5

    # Success removes an entry
    registry.record_success("ev_0")
    assert len(registry._entries) == 4

    # Purge expired
    registry.record_failure("ev_1", t0)  # reaches threshold (2 failures), quarantined until t0 + 10.0
    assert registry.is_quarantined("ev_1", t0) is True

    # After t0 + 15, purge_expired cleans ev_1
    registry.purge_expired(t0 + 15.0)
    assert "ev_1" not in registry._entries


@pytest.mark.asyncio
async def test_commit_error_on_mark_events_dispatched(db_session, monkeypatch):
    """When mark_events_dispatched fails (DB error), it is recorded as a failure and does not hot loop."""
    stop_event = asyncio.Event()
    wake_event = asyncio.Event()

    ev = RealtimeEvent(squad_id=1, payload={"msg": "commit_fail"})
    db_session.add(ev)
    db_session.commit()

    broadcast_mock = AsyncMock()
    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", broadcast_mock)

    # Force mark_events_dispatched to return False
    monkeypatch.setattr("core.realtime_relay.mark_events_dispatched", Mock(return_value=False))

    quarantine = QuarantineRegistry(threshold=2, duration=100.0)
    mock_listener = Mock()
    mock_listener.stop_async = AsyncMock(return_value=True)

    loop_task = asyncio.create_task(
        realtime_relay_loop(
            stop_event=stop_event,
            wake_event=wake_event,
            listener=mock_listener,
            quarantine=quarantine,
        )
    )

    # Allow initial pass to execute
    await asyncio.sleep(0.05)
    stop_event.set()
    await loop_task

    # Event failed to mark dispatched, so it must be recorded in quarantine as failure
    assert ev.id in quarantine._entries
    assert quarantine._entries[ev.id]["failures"] >= 1
    db_session.refresh(ev)
    assert ev.dispatched_at is None


@pytest.mark.asyncio
async def test_bounded_stop_idle_loop():
    """Calling stop_event on an idle relay loop terminates cleanly in < 0.3s."""
    stop_event = asyncio.Event()
    wake_event = asyncio.Event()

    mock_listener = Mock()
    mock_listener.stop_async = AsyncMock(return_value=True)

    loop_task = asyncio.create_task(
        realtime_relay_loop(stop_event=stop_event, wake_event=wake_event, listener=mock_listener)
    )

    await asyncio.sleep(0.05)
    start_time = time.monotonic()
    stop_event.set()
    await loop_task
    duration = time.monotonic() - start_time

    assert duration < 0.3, f"Idle loop shutdown took too long: {duration:.2f}s"
    mock_listener.stop_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_monotonic_purge_interval(db_session, monkeypatch):
    """Purge of 7-day old events is called only when monotonic time advances by >= 60.0s."""
    stop_event = asyncio.Event()
    wake_event = asyncio.Event()

    purge_call_count = 0

    def mock_purge():
        nonlocal purge_call_count
        purge_call_count += 1

    monkeypatch.setattr("core.realtime_relay.purge_old_events", mock_purge)

    mock_listener = Mock()
    mock_listener.stop_async = AsyncMock(return_value=True)

    fake_monotonic = 100.0

    def fake_clock():
        return fake_monotonic

    loop_task = asyncio.create_task(
        realtime_relay_loop(
            stop_event=stop_event,
            wake_event=wake_event,
            listener=mock_listener,
            clock=fake_clock,
        )
    )

    await asyncio.sleep(0.05)
    # fake_monotonic is still 100.0, elapsed = 0 -> no purge
    wake_event.set()
    await asyncio.sleep(0.05)
    assert purge_call_count == 0

    # Advance monotonic time by 65s
    fake_monotonic = 165.0
    wake_event.set()
    await asyncio.sleep(0.05)
    assert purge_call_count == 1

    # Advance by 10s -> still under 60s from last purge (165 + 10 = 175) -> no additional purge
    fake_monotonic = 175.0
    wake_event.set()
    await asyncio.sleep(0.05)
    assert purge_call_count == 1

    stop_event.set()
    await loop_task
