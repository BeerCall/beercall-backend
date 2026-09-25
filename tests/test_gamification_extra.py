import pytest
from datetime import datetime, timezone, timedelta
from services.gamification import handle_ia_fraud, award_badge, check_and_award_ghost_badges, apply_apero_start_rewards, run_daily_apero_checks
from models.user import User
from models.apero import Apero, AperoStatus, AperoParticipant, ParticipationStatus
from models.gamification import Badge

def test_gamification_functions(db_session):
    # Seed badge
    badge = Badge(id="FAUSSAIRE", name="Faussaire", description="desc", icon="X")
    badge2 = Badge(id="BAPTEME", name="Bapteme", description="desc", icon="X")
    badge3 = Badge(id="REMI_SANS_AMIS", name="Remi", description="desc", icon="X")
    badge4 = Badge(id="FLOP_PERSONNE_N_EST_VENU", name="Flop", description="desc", icon="X")
    db_session.add_all([badge, badge2, badge3, badge4])
    db_session.commit()

    u = User(id=100, username="gamif", hashed_password="pw", capsules=100, ia_fraud_count=0)
    db_session.add(u)
    db_session.commit()
    
    # handle_ia_fraud
    handle_ia_fraud(u, db_session)
    assert u.ia_fraud_count == 1
    assert u.capsules == 85

    handle_ia_fraud(u, db_session)
    handle_ia_fraud(u, db_session)
    db_session.commit()
    db_session.refresh(u)
    
    # Check ghost badges
    u.consecutive_piscine = 5
    db_session.commit()
    streak = check_and_award_ghost_badges(u, db_session)
    assert streak == 0 # no absences yet
    
    # award badge direct
    award_badge(u, "BAPTEME", db_session)

    apero = Apero(id=100, squad_id=100, creator_id=100, latitude=48.8, longitude=2.3, location_name="Loc")
    db_session.add(apero)
    db_session.commit()

    apply_apero_start_rewards(u, apero, db_session)
    assert u.capsules > 85

def test_daily_apero_checks(db_session):
    badge3 = Badge(id="REMI_SANS_AMIS", name="Remi", description="desc", icon="X")
    badge4 = Badge(id="FLOP_PERSONNE_N_EST_VENU", name="Flop", description="desc", icon="X")
    # avoid duplicates if previous test already added, but it's isolated per function so it's fine
    db_session.add_all([badge3, badge4])

    u1 = User(id=101, username="u1", hashed_password="pw", capsules=100)
    db_session.add(u1)
    db_session.commit()

    now = datetime.now(timezone.utc)
    
    # Ended with 1 participant (remi)
    apero1 = Apero(
        id=101, squad_id=1, creator_id=101, status=AperoStatus.ENDED, latitude=48.8, longitude=2.3, location_name="Loc",
        ended_at=now - timedelta(hours=1), daily_check_processed_at=None
    )
    p1 = AperoParticipant(apero_id=101, user_id=101, status=ParticipationStatus.JOINED)

    # Scheduled but time passed, no participants (flop)
    apero2 = Apero(
        id=102, squad_id=1, creator_id=101, status=AperoStatus.SCHEDULED, latitude=48.8, longitude=2.3, location_name="Loc",
        scheduled_for=now - timedelta(hours=1), daily_check_processed_at=None
    )

    db_session.add_all([apero1, apero2, p1])
    db_session.commit()

    processed = run_daily_apero_checks(db_session, now)
    assert processed == 2
