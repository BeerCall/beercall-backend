import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.database import Base
from models.user import User
from models.apero import Apero, AperoStatus, AperoParticipant, ParticipationStatus
from services.apero_lifecycle import validate_distance, APERO_DURATION, MAX_START_DISTANCE_METERS, get_apero_for_squad, get_squad_member

def test_apero_lifecycle_methods(db_session):
    user = User(username="lifecycleuser", hashed_password="pw", avatar_config={})
    db_session.add(user)
    db_session.commit()
    
    apero = Apero(
        squad_id=999,
        creator_id=user.id,
        location_name="Test Loc",
        latitude=48.8,
        longitude=2.3,
        status=AperoStatus.SCHEDULED,
        scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    db_session.add(apero)
    db_session.commit()
    
    dist = validate_distance(apero, 48.8, 2.3)
    assert dist < MAX_START_DISTANCE_METERS

    dist2 = validate_distance(apero, 48.9, 2.4)
    assert dist2 > MAX_START_DISTANCE_METERS
