from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from models.apero import Apero, AperoStatus, AperoParticipant, ParticipationStatus
from models.squad import Squad
from services.photo_validation import calculate_geodistance

APERO_DURATION = timedelta(hours=4)
MAX_START_DISTANCE_METERS = 500


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_apero_active(apero: Apero, now: Optional[datetime] = None) -> bool:
    now = now or utc_now()
    return apero.status == AperoStatus.ACTIVE and (apero.ended_at is None or aware(apero.ended_at) > now)


def can_start_scheduled(apero: Apero, now: Optional[datetime] = None) -> bool:
    return apero.status == AperoStatus.SCHEDULED and apero.scheduled_for is not None


def get_squad_member(db: Session, squad_id: int, user_id: int) -> Squad:
    squad = db.query(Squad).filter(Squad.id == squad_id).first()
    if not squad:
        raise ValueError("Squad introuvable")
    if not any(member.id == user_id for member in squad.members):
        raise PermissionError("Tu ne fais pas partie de cette Squad")
    return squad


def get_apero_for_squad(db: Session, squad_id: int, apero_id: int) -> Apero:
    apero = db.query(Apero).filter(Apero.id == apero_id, Apero.squad_id == squad_id).first()
    if not apero:
        raise LookupError("Apéro introuvable dans cette squad")
    return apero


def validate_distance(apero: Apero, latitude: float, longitude: float) -> float:
    return calculate_geodistance(latitude, longitude, apero.latitude, apero.longitude)


def start_scheduled_apero(db: Session, apero_id: int, user_id: int, photo_path: str, now: Optional[datetime] = None):
    now = now or utc_now()
    # Lock the row on PostgreSQL; the conditional status check remains safe on SQLite.
    query = db.query(Apero).filter(Apero.id == apero_id, Apero.status == AperoStatus.SCHEDULED)
    if db.bind and db.bind.dialect.name != "sqlite":
        query = query.with_for_update()
    apero = query.first()
    if not apero:
        return None, "already_started"

    apero.status = AperoStatus.ACTIVE
    apero.started_at = now
    apero.ended_at = now + APERO_DURATION
    apero.photo_path = photo_path
    participant = AperoParticipant(
        apero_id=apero.id, user_id=user_id, status=ParticipationStatus.JOINED, photo_path=photo_path
    )
    db.add(participant)
    db.flush()
    return apero, "started"
