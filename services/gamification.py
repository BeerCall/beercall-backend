# Fichier: services/gamification.py
from sqlalchemy.orm import Session
from models.user import User
from models.gamification import Badge


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
