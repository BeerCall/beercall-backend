import os
import uuid
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.apero import Apero, AperoParticipant, AperoStatus, ParticipationStatus
from models.squad import Squad
from models.user import User
from services.gamification import apply_beer_call_join_rewards, award_badge, handle_ia_fraud
from services.photo_validation import calculate_geodistance


class ParticipationConflict(ValueError):
    """Participation or apero state changed concurrently."""


def join_beer_call(
    db: Session, user: User, squad_id: int, apero_id: int,
    latitude: float, longitude: float, file_bytes: bytes,
    detector: Callable[[bytes], bool],
) -> int:
    existing = db.query(AperoParticipant).filter(
        AperoParticipant.apero_id == apero_id, AperoParticipant.user_id == user.id,
    ).first()
    if existing:
        raise ValueError("Tu as déjà répondu à cet appel de la bière !")
    apero = db.get(Apero, apero_id)
    squad = db.get(Squad, squad_id)
    if not apero or apero.squad_id != squad_id:
        raise LookupError("Apéro introuvable dans cette squad")
    if not squad or user not in squad.members:
        raise PermissionError("Tu ne fais pas partie de cette Squad")
    if apero.status != AperoStatus.ACTIVE:
        raise ParticipationConflict("Cet apéro n'est pas actif")
    distance = calculate_geodistance(latitude, longitude, apero.latitude, apero.longitude)
    if distance > 500:
        handle_ia_fraud(user, db)
        db.commit()
        raise PermissionError(f"Triche détectée ! Tu es à {int(distance)}m. Malus appliqué. 📉")
    if not detector(file_bytes):
        handle_ia_fraud(user, db)
        db.commit()
        raise ValueError("Pas de boisson, pas de Bar ! -15 Caps 📉")
    os.makedirs("uploads/aperos", exist_ok=True)
    file_path = f"uploads/aperos/reply_{uuid.uuid4()}.jpg"
    try:
        with open(file_path, "wb") as output:
            output.write(file_bytes)
        total_gained = apply_beer_call_join_rewards(user, apero, db)
        db.add(AperoParticipant(
            apero_id=apero_id, user_id=user.id,
            status=ParticipationStatus.JOINED, photo_path=file_path,
        ))
        db.commit()
        return total_gained
    except Exception as exc:
        db.rollback()
        if os.path.exists(file_path):
            os.remove(file_path)
        if isinstance(exc, IntegrityError):
            raise ParticipationConflict("Tu as déjà rejoint cet apéro !") from exc
        raise


def decline_beer_call(db: Session, user: User, squad_id: int, apero_id: int, excuse: str | None) -> None:
    squad = db.get(Squad, squad_id)
    apero = db.get(Apero, apero_id)
    if not squad or user not in squad.members:
        raise PermissionError("Tu ne fais pas partie de cette Squad")
    if not apero or apero.squad_id != squad_id:
        raise LookupError("Apéro introuvable dans cette squad")
    if apero.status != AperoStatus.ACTIVE:
        raise ParticipationConflict("Cet apéro n'est pas actif")
    existing = db.query(AperoParticipant).filter(
        AperoParticipant.apero_id == apero_id, AperoParticipant.user_id == user.id,
    ).first()
    if existing:
        raise ValueError("Tu as déjà répondu à cet appel de la bière !")
    participant = AperoParticipant(
        apero_id=apero_id, user_id=user.id, status=ParticipationStatus.DECLINED, excuse=excuse,
    )
    try:
        user.capsules += 15
        user.consecutive_joins = 0
        user.consecutive_piscine += 1
        user.consecutive_declines = user.consecutive_declines + 1 if excuse else 0
        if user.consecutive_piscine >= 5:
            award_badge(user, "NAGEUR", db)
        if user.consecutive_declines >= 5:
            award_badge(user, "CASANIER", db)
        db.add(participant)
        db.commit()
    except Exception:
        db.rollback()
        raise
