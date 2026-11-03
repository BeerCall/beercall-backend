from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from models.user import User
from services.apero_lifecycle import get_squad_member
from services.avatar_sprites import avatar_sprites_for
from services.photo_validation import calculate_geodistance


def update_last_location(
    db: Session, user: User, latitude: float, longitude: float
) -> bool:
    """
    Met à jour la dernière position d'un utilisateur en UTC serveur.
    Applique un throttling bidimensionnel : no-op si < 30s ET distance < 25m.
    Ne commite pas en base (délégué au routeur selon les conventions).
    """
    now = datetime.now(timezone.utc)

    if user.last_location_at is not None and user.last_latitude is not None and user.last_longitude is not None:
        last_at = user.last_location_at
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)

        delta_sec = (now - last_at).total_seconds()
        if delta_sec < 30.0:
            dist = calculate_geodistance(user.last_latitude, user.last_longitude, latitude, longitude)
            if dist < 25.0:
                return False

    user.last_latitude = latitude
    user.last_longitude = longitude
    user.last_location_at = now
    return True


def get_squad_member_locations(
    db: Session, squad_id: int, user_id: int
) -> List[Dict[str, Any]]:
    """
    Récupère la liste des dernières positions connues des membres d'une squad.
    Lève LookupError si la squad est introuvable (404),
    ou PermissionError si l'utilisateur n'en fait pas partie (403).
    Trié par date décroissante puis id croissant. Exclut les membres non localisés.
    """
    try:
        squad = get_squad_member(db, squad_id, user_id)
    except ValueError as exc:
        raise LookupError("Squad introuvable") from exc

    located_members = [
        m for m in squad.members
        if m.last_latitude is not None and m.last_longitude is not None and m.last_location_at is not None
    ]

    # Tri par date décroissante puis par id croissant
    def sort_key(member: User):
        loc_at = member.last_location_at
        if loc_at.tzinfo is None:
            loc_at = loc_at.replace(tzinfo=timezone.utc)
        return (loc_at, -member.id)

    located_members.sort(key=sort_key, reverse=True)

    result = []
    for m in located_members:
        loc_at = m.last_location_at
        if loc_at.tzinfo is None:
            loc_at = loc_at.replace(tzinfo=timezone.utc)
        result.append({
            "user_id": m.id,
            "username": m.username,
            "latitude": m.last_latitude,
            "longitude": m.last_longitude,
            "located_at": loc_at,
            "avatar_sprites": avatar_sprites_for(m),
        })

    return result
