import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models.apero import Apero, AperoParticipant, AperoStatus, ParticipationStatus
from models.gamification import Badge
from models.user import User

logger = logging.getLogger(__name__)


def award_badge(user: User, badge_id: str, db: Session):
    if not any(b.id == badge_id for b in user.badges):
        badge = db.query(Badge).filter(Badge.id == badge_id).first()
        if badge:
            user.badges.append(badge)


def handle_ia_fraud(user: User, db: Session):
    user.capsules = max(0, user.capsules - 15)
    user.ia_fraud_count += 1
    if user.ia_fraud_count >= 3:
        award_badge(user, "FAUSSAIRE", db)


def apply_apero_start_rewards(user: User, apero: Apero, db: Session):
    """Apply creation/presence rewards exactly once, after a successful start."""
    created_count = db.query(Apero).filter(Apero.creator_id == user.id).count()
    joined_count = db.query(AperoParticipant).filter(
        AperoParticipant.user_id == user.id,
        AperoParticipant.status == ParticipationStatus.JOINED,
    ).count()
    user.capsules += 50
    user.consecutive_joins += 1
    user.consecutive_declines = 0
    user.consecutive_piscine = 0
    for threshold, badge_id in ((1, "ETINCELLE"), (10, "RABATTEUR"), (50, "AUBERGISTE"), (100, "DIEU_FETE")):
        if created_count >= threshold:
            award_badge(user, badge_id, db)
    for threshold, badge_id in ((1, "BAPTEME"), (10, "HABITUE"), (50, "PILIER"), (100, "LEGENDE")):
        if joined_count >= threshold:
            award_badge(user, badge_id, db)
    if user.consecutive_joins >= 3:
        award_badge(user, "MARATHONIEN", db)
    if user.consecutive_joins >= 10:
        award_badge(user, "INCREVABLE", db)


def check_and_award_ghost_badges(current_user, db: Session) -> int:
    squad_start_dates = {}
    for apero in current_user.aperos_created:
        if apero.status in (AperoStatus.ACTIVE, AperoStatus.ENDED):
            squad_start_dates[apero.squad_id] = min(squad_start_dates.get(apero.squad_id, apero.created_at), apero.created_at)
    for participation in current_user.participations:
        if participation.status == ParticipationStatus.JOINED:
            apero = participation.apero
            squad_start_dates[apero.squad_id] = min(squad_start_dates.get(apero.squad_id, apero.created_at), apero.created_at)
    if not squad_start_dates:
        return 0
    squad_ids = [s.id for s in current_user.squads]
    closed_aperos = db.query(Apero).filter(
        Apero.squad_id.in_(squad_ids), Apero.status == AperoStatus.ENDED,
        Apero.ended_at <= datetime.now(timezone.utc)
    ).order_by(Apero.ended_at.desc()).all()
    participated = {p.apero_id for p in db.query(AperoParticipant).filter(AperoParticipant.user_id == current_user.id)}
    streak = 0
    for apero in closed_aperos:
        if apero.created_at < squad_start_dates.get(apero.squad_id, apero.created_at):
            continue
        if apero.id in participated:
            break
        streak += 1
    if streak >= 10:
        award_badge(current_user, "SOMNAMBULE", db)
    return streak


def run_daily_apero_checks(db: Session, now: datetime | None = None):
    """Idempotent processing of recent ended aperos and scheduled flops."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=240000)
    ended = db.query(Apero).filter(
        Apero.status == AperoStatus.ENDED,
        Apero.ended_at >= since, Apero.ended_at <= now,
        Apero.daily_check_processed_at.is_(None),
    ).all()
    scheduled = db.query(Apero).filter(
        Apero.status == AperoStatus.SCHEDULED,
        Apero.scheduled_for >= since, Apero.scheduled_for <= now,
        Apero.daily_check_processed_at.is_(None),
    ).all()
    processed = 0
    for apero in ended:
        joined = [p for p in apero.participants if p.status == ParticipationStatus.JOINED]
        if len(joined) == 1:
            award_badge(joined[0].user, "REMI_SANS_AMIS", db)
        apero.daily_check_processed_at = now
        processed += 1
        logger.info("daily_apero_check apero_id=%s decision=ended joined=%s", apero.id, len(joined))
    for apero in scheduled:
        joined = [p for p in apero.participants if p.status == ParticipationStatus.JOINED]
        if joined:
            logger.warning("daily_apero_check apero_id=%s decision=skip_inconsistent joined=%s", apero.id, len(joined))
            apero.daily_check_processed_at = now
            continue
        award_badge(apero.creator, "FLOP_PERSONNE_N_EST_VENU", db)
        apero.creator.capsules = max(0, apero.creator.capsules - 15)
        apero.daily_check_processed_at = now
        for participant in list(apero.participants):
            db.delete(participant)
        db.delete(apero)
        processed += 1
        logger.info("daily_apero_check apero_id=%s decision=flop creator_id=%s", apero.id, apero.creator_id)
    db.commit()
    return processed
