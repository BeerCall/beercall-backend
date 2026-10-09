import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from models.realtime_event import RealtimeEvent
from core.realtime_relay import fetch_events_to_dispatch, mark_events_dispatched, purge_old_events, realtime_relay_loop
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
    # Appeler la fonction
    results = fetch_events_to_dispatch()
    
    # 2 événements devraient être remontés
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
    from unittest.mock import AsyncMock, Mock
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

def test_purge_old_events(db_session):
    # Créer un événement récent
    recent = RealtimeEvent(squad_id=1, payload={"msg": "recent"}, dispatched_at=datetime.now(timezone.utc))
    # Créer un événement ancien
    old = RealtimeEvent(squad_id=1, payload={"msg": "old"}, created_at=datetime.now(timezone.utc) - timedelta(days=8), dispatched_at=datetime.now(timezone.utc) - timedelta(days=8))
    
    db_session.add_all([recent, old])
    db_session.commit()
    
    old_id = old.id
    recent_id = recent.id
    
    purge_old_events()
    
    # Vérifier que old a été supprimé
    assert db_session.query(RealtimeEvent).filter(RealtimeEvent.id == old_id).first() is None
    # Vérifier que recent est toujours là
    assert db_session.query(RealtimeEvent).filter(RealtimeEvent.id == recent_id).first() is not None

@pytest.mark.asyncio
async def test_realtime_relay_loop(db_session, test_events):
    stop_event = asyncio.Event()
    
    # On crée une task
    task = asyncio.create_task(realtime_relay_loop(stop_event))
    
    # On laisse la task tourner un tout petit peu
    await asyncio.sleep(0.5)
    
    # On arrête la task
    stop_event.set()
    await task
    
    # Vérifier que les events ont été dispatchés
    for ev in test_events:
        db_session.refresh(ev)
        assert ev.dispatched_at is not None
