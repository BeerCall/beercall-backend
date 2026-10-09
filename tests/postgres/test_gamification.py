import pytest
from datetime import datetime, timezone, timedelta
from models.apero import Apero, AperoStatus
from models.user import User
from models.squad import Squad
from models.gamification import Badge
from services.gamification import run_daily_apero_checks

@pytest.fixture
def test_data(db_session):
    u = User(username="gamif_user", hashed_password="pw")
    s = Squad(name="gamif_squad", invite_code="GMF")
    s.members.append(u)
    
    # We need the flop badge to exist for the test
    b = Badge(id="FLOP_PERSONNE_N_EST_VENU", name="flop", description="flop")
    
    db_session.add_all([u, s, b])
    db_session.commit()
    db_session.refresh(u)
    db_session.refresh(s)
    return u, s

def test_run_daily_apero_checks_flop(db_session, test_data):
    u, s = test_data
    now = datetime.now(timezone.utc)
    
    # Un flop scheduled for 5 hours ago
    scheduled_apero = Apero(
        creator_id=u.id,
        squad_id=s.id,
        status=AperoStatus.SCHEDULED,
        scheduled_for=now - timedelta(hours=5),
        latitude=48.8,
        longitude=2.3,
        location_name="Flop Bar"
    )
    
    # Un scheduled mais qui est récent (2h ago) => devrait pas flop
    recent_scheduled = Apero(
        creator_id=u.id,
        squad_id=s.id,
        status=AperoStatus.SCHEDULED,
        scheduled_for=now - timedelta(hours=2),
        latitude=48.8,
        longitude=2.3,
        location_name="Recent Bar"
    )
    
    db_session.add_all([scheduled_apero, recent_scheduled])
    db_session.commit()
    
    # Act
    processed = run_daily_apero_checks(db_session, now)
    
    # Assert: only 1 should be processed
    assert processed == 1
    
    db_session.refresh(u)
    assert any(b.id == "FLOP_PERSONNE_N_EST_VENU" for b in u.badges)
    
    # verify the old one is deleted
    assert db_session.query(Apero).filter(Apero.id == scheduled_apero.id).first() is None
    
    # verify the recent one is untouched
    assert db_session.query(Apero).filter(Apero.id == recent_scheduled.id).first() is not None


def test_daily_check_skips_apero_locked_for_start(db_session, test_data, postgres_engine):
    from sqlalchemy.orm import Session
    from services.apero_lifecycle import start_scheduled_apero
    user, squad = test_data
    now = datetime.now(timezone.utc)
    apero = Apero(creator_id=user.id, squad_id=squad.id, status=AperoStatus.SCHEDULED,
                  scheduled_for=now - timedelta(hours=5), latitude=48.8, longitude=2.3)
    db_session.add(apero)
    db_session.commit()
    apero_id = apero.id
    with Session(postgres_engine) as starter, Session(postgres_engine) as daily:
        starter.query(Apero).filter(Apero.id == apero_id).with_for_update().one()
        assert run_daily_apero_checks(daily, now) == 0
        started, result = start_scheduled_apero(starter, apero_id, user.id, "test.jpg", now)
        assert result == "started"
        starter.commit()
    db_session.expire_all()
    assert db_session.get(Apero, apero_id).status == AperoStatus.ACTIVE
    db_session.refresh(user)
    assert not user.badges
