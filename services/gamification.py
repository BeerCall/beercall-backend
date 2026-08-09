# Fichier: services/gamification.py
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models.gamification import Badge
from models.user import User


def award_badge(user: User, badge_id: str, db: Session):
    """Vérifie si le joueur a le badge, sinon lui donne."""
    has_badge = any(b.id == badge_id for b in user.badges)
    if not has_badge:
        badge = db.query(Badge).filter(Badge.id == badge_id).first()
        if badge:
            user.badges.append(badge)


def handle_ia_fraud(user: User, db: Session):
    """Applique le malus de capsule et vérifie le badge Faussaire"""
    # Malus de 15 caps (sans descendre en dessous de 0)
    user.capsules = max(0, user.capsules - 15)
    user.ia_fraud_count += 1

    if user.ia_fraud_count >= 3:
        award_badge(user, "FAUSSAIRE", db)


def check_and_award_ghost_badges(current_user, db: Session) -> int:
    """
    Calcule la série actuelle de 'Fantôme' (apéros ignorés).
    Attribue le badge SOMNAMBULE si la série atteint 10.
    Retourne le nombre consécutif de ghosts.
    """
    squad_ids = [s.id for s in current_user.squads]
    if not squad_ids:
        return 0

    # Récupérer les apéros terminés (vieux de plus de 4h) triés du plus récent au plus ancien
    four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=4)
    closed_aperos = db.query(Apero).filter(
        Apero.squad_id.in_(squad_ids),
        Apero.created_at <= four_hours_ago
    ).order_by(Apero.created_at.desc()).all()

    # Dictionnaire des participations de l'utilisateur (pour une recherche instantanée)
    participations = {
        p.apero_id: p.status
        for p in db.query(AperoParticipant).filter(AperoParticipant.user_id == current_user.id).all()
    }

    ghost_streak = 0
    for a in closed_aperos:
        if a.id in participations:
            # Dès qu'on trouve un apéro où il a répondu (Join ou Decline), la série fantôme s'arrête
            break
        ghost_streak += 1

    # Attribution du badge Somnambule
    if ghost_streak >= 10:
        award_badge(current_user, "SOMNAMBULE", db)
        # db.commit() est géré par la route appelante

    return ghost_streak
