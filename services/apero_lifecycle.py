from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from models.apero import Apero, AperoStatus, AperoParticipant, ParticipationStatus
from models.squad import Squad
from models.user import User
from schemas.apero import ScheduledAperoCreate
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


def schedule_apero(db: Session, user: User, squad_id: int, data: ScheduledAperoCreate) -> Apero:
    try:
        get_squad_member(db, squad_id, user.id)
    except ValueError as exc:
        raise LookupError(str(exc)) from exc
    scheduled_for = data.scheduled_for.astimezone(timezone.utc)
    if scheduled_for <= utc_now():
        raise ValueError("La date doit être dans le futur")
    existing_aperos = db.query(Apero).filter(
        Apero.squad_id == squad_id,
        Apero.status.in_([AperoStatus.SCHEDULED, AperoStatus.ACTIVE]),
    ).all()
    for existing in existing_aperos:
        existing_time = existing.scheduled_for if existing.status == AperoStatus.SCHEDULED else (existing.started_at or existing.created_at)
        if existing_time is not None:
            time_diff = abs((scheduled_for - aware(existing_time)).total_seconds())
            if time_diff < 4 * 3600 and calculate_geodistance(data.latitude, data.longitude, existing.latitude, existing.longitude) <= 500:
                status = "programmé" if existing.status == AperoStatus.SCHEDULED else "en cours"
                raise ValueError(f"Un apéro est déjà {status} à cet endroit dans ce créneau horaire")
    apero = Apero(
        squad_id=squad_id, creator_id=user.id, location_name=data.location_name,
        latitude=data.latitude, longitude=data.longitude,
        status=AperoStatus.SCHEDULED, scheduled_for=scheduled_for,
    )
    db.add(apero)
    db.commit()
    db.refresh(apero)
    return apero


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
