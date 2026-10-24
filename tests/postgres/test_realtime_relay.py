import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock
from models.realtime_event import RealtimeEvent
from core.realtime_relay import (
    fetch_events_to_dispatch,
    mark_events_dispatched,
    purge_old_events,
    realtime_relay_loop,
)
from models.squad import Squad
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def isolated_relay(db_session, monkeypatch):
    monkeypatch.setattr("core.realtime_relay.SessionLocal", sessionmaker(bind=db_session.get_bind()))
    db_session.add(Squad(id=1, name="Relay tests", invite_code="relay-test"))
    db_session.commit()


@pytest.fixture
def test_events(db_session):
    event1 = RealtimeEvent(squad_id=1, payload={"msg": "hello 1"})
    event2 = RealtimeEvent(squad_id=1, payload={"msg": "hello 2"})
    db_session.add_all([event1, event2])
    db_session.commit()
    return [event1, event2]


def test_fetch_and_lock_events(db_session, test_events):
    results = fetch_events_to_dispatch()
    assert len(results) == 2

    # Fetch must never acknowledge delivery before broadcasting.
    for ev in test_events:
        db_session.refresh(ev)
        assert ev.dispatched_at is None
    mark_events_dispatched([ev.id for ev in test_events])
    for ev in test_events:
        db_session.refresh(ev)
        assert ev.dispatched_at is not None


@pytest.mark.asyncio
async def test_failed_push_is_not_acknowledged_and_other_events_progress(db_session, monkeypatch):
    failed = RealtimeEvent(squad_id=1, payload={"type": "REFRESH_SQUAD", "action": "CREATE"})
    successful = RealtimeEvent(squad_id=1, payload={"type": "REFRESH_SQUAD", "action": "REJECTED"})
    db_session.add_all([failed, successful])
    db_session.commit()

    stop = asyncio.Event()
    broadcast = AsyncMock()
    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", broadcast)

    def fail_push(event_id):
        stop.set()
        raise RuntimeError("Firebase unavailable")

    monkeypatch.setattr("core.realtime_relay.dispatch_pushes", Mock(side_effect=fail_push))
    await realtime_relay_loop(stop)

    db_session.refresh(failed)
    db_session.refresh(successful)
    assert failed.dispatched_at is None
    assert successful.dispatched_at is not None
    assert broadcast.await_count == 2


@pytest.mark.asyncio
async def test_broadcast_failure_retains_event_for_retry(db_session, monkeypatch):
    """When websocket broadcast fails, event is retained without dispatched_at acknowledgement."""
    event = RealtimeEvent(squad_id=1, payload={"type": "BEER_CALL_ALERT", "msg": "urgent"})
    db_session.add(event)
    db_session.commit()

    stop = asyncio.Event()

    async def fail_broadcast(squad_id, payload):
        stop.set()
        raise ConnectionResetError("Websocket transport broken")

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", fail_broadcast)
    await realtime_relay_loop(stop)

    db_session.refresh(event)
    assert event.dispatched_at is None


@pytest.mark.asyncio
async def test_push_private_fields_never_broadcast(db_session, monkeypatch):
    """Private push_* keys (push_recipients, push_completed, etc.) must NEVER be broadcast over websocket."""
    event = RealtimeEvent(
        squad_id=1,
        payload={
            "type": "REFRESH_SQUAD",
            "action": "CREATE",
            "public_key": "visible_to_all",
            "push_recipients": [10, 20, 30],
            "push_completed": [10],
            "push_internal_token": "secret_fcm",
        }
    )
    db_session.add(event)
    db_session.commit()

    broadcast_payloads = []
    stop = asyncio.Event()

    async def record_broadcast(squad_id, payload):
        broadcast_payloads.append(dict(payload))
        stop.set()

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", record_broadcast)
    monkeypatch.setattr("core.realtime_relay.dispatch_pushes", Mock())

    await realtime_relay_loop(stop)

    assert len(broadcast_payloads) == 1
    sent = broadcast_payloads[0]
    assert sent["type"] == "REFRESH_SQUAD"
    assert sent["action"] == "CREATE"
    assert sent["public_key"] == "visible_to_all"
    for key in sent:
        assert not key.startswith("push_"), f"Private push field leaked in broadcast: {key}"


@pytest.mark.asyncio
async def test_relay_restart_resumes_undispatched_events(db_session, monkeypatch):
    """An event retained from a failed loop pass is resumed and dispatched upon relay restart."""
    event = RealtimeEvent(squad_id=1, payload={"msg": "resumable"})
    db_session.add(event)
    db_session.commit()

    # Pass 1: Broadcast fails, event retained
    stop1 = asyncio.Event()
    async def fail_broadcast(squad_id, payload):
        stop1.set()
        raise RuntimeError("Transient network blip")

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", fail_broadcast)
    await realtime_relay_loop(stop1)

    db_session.refresh(event)
    assert event.dispatched_at is None

    # Pass 2: Relay restarted, transient blip resolved
    stop2 = asyncio.Event()
    async def ok_broadcast(squad_id, payload):
        stop2.set()

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", ok_broadcast)
    await realtime_relay_loop(stop2)

    db_session.refresh(event)
    assert event.dispatched_at is not None


def test_purge_old_events(db_session):
    recent = RealtimeEvent(squad_id=1, payload={"msg": "recent"}, dispatched_at=datetime.now(timezone.utc))
    old = RealtimeEvent(
        squad_id=1,
        payload={"msg": "old"},
        created_at=datetime.now(timezone.utc) - timedelta(days=8),
        dispatched_at=datetime.now(timezone.utc) - timedelta(days=8)
    )

    db_session.add_all([recent, old])
    db_session.commit()

    old_id = old.id
    recent_id = recent.id

    purge_old_events()

    assert db_session.query(RealtimeEvent).filter(RealtimeEvent.id == old_id).first() is None
    assert db_session.query(RealtimeEvent).filter(RealtimeEvent.id == recent_id).first() is not None


@pytest.mark.asyncio
async def test_realtime_relay_loop(db_session, test_events, monkeypatch):
    """Deterministic relay loop execution without long sleep."""
    stop_event = asyncio.Event()
    dispatched_count = 0

    async def counted_broadcast(squad_id, payload):
        nonlocal dispatched_count
        dispatched_count += 1
        if dispatched_count >= len(test_events):
            stop_event.set()

    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", counted_broadcast)
    await realtime_relay_loop(stop_event)

    for ev in test_events:
        db_session.refresh(ev)
        assert ev.dispatched_at is not None
