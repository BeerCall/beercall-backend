import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from models.realtime_event import RealtimeEvent
from core.realtime_relay import fetch_and_lock_events, purge_old_events, realtime_relay_loop

@pytest.fixture
def test_events(db_session):
    event1 = RealtimeEvent(squad_id=1, payload={"msg": "hello 1"})
    event2 = RealtimeEvent(squad_id=1, payload={"msg": "hello 2"})
    db_session.add_all([event1, event2])
    db_session.commit()
    return [event1, event2]

def test_fetch_and_lock_events(db_session, test_events):
    # Appeler la fonction
    results = fetch_and_lock_events()
    
    # 2 événements devraient être remontés
    assert len(results) == 2
    
    # Vérifier que les événements ont bien dispatched_at mis à jour en base
    for ev in test_events:
        db_session.refresh(ev)
        assert ev.dispatched_at is not None

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
