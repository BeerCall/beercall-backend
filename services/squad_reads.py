"""Squad read service for optimized summary details.

Aggregates apero participants and project current user participation using bounded queries
without hydrating AperoParticipant collections.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from models.apero import Apero, AperoParticipant, AperoStatus, ParticipationStatus
from models.squad import Squad


def force_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure datetime has explicit UTC timezone."""
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_squad_details_summary(
    db: Session,
    squad: Squad,
    current_user_id: int,
) -> Dict[str, Any]:
    """Build squad details summary:
    - Loads aperos ordered by created_at.desc() with creator joinedload, without participants collection.
    - Computes JOINED participant counts per apero via 1 bounded aggregate query.
    - Projects current user participation status per apero via 1 bounded projection query.
    - Performs automatic expiration of active aperos older than 4h or past ended_at.
    - Materializes all response scalars before commit to avoid post-commit lazy loading.
    - Commits DB and returns dict matching SquadDetailsResponse.
    """
    squad_id = squad.id

    # 1. Fetch aperos ordered by created_at desc with eager-loaded creator (no participants collection)
    aperos = (
        db.query(Apero)
        .options(joinedload(Apero.creator))
        .filter(Apero.squad_id == squad_id)
        .order_by(Apero.created_at.desc())
        .all()
    )

    # 2. Bounded aggregate: count JOINED participants for each apero in this squad
    joined_counts_rows = (
        db.query(AperoParticipant.apero_id, func.count(AperoParticipant.id))
        .join(Apero, Apero.id == AperoParticipant.apero_id)
        .filter(
            Apero.squad_id == squad_id,
            AperoParticipant.status == ParticipationStatus.JOINED,
        )
        .group_by(AperoParticipant.apero_id)
        .all()
    )
    joined_counts = {row[0]: row[1] for row in joined_counts_rows}

    # 3. Bounded projection: current user's participation status per apero in this squad
    user_part_rows = (
        db.query(AperoParticipant.apero_id, AperoParticipant.status)
        .join(Apero, Apero.id == AperoParticipant.apero_id)
        .filter(
            Apero.squad_id == squad_id,
            AperoParticipant.user_id == current_user_id,
        )
        .all()
    )
    user_parts = {row[0]: row[1] for row in user_part_rows}

    # 4. Process expiration and build serialized items before commit
    now = datetime.now(timezone.utc)
    active_beer_call: List[Dict[str, Any]] = []
    scheduled_beer_calls: List[Dict[str, Any]] = []
    past_beer_calls: List[Dict[str, Any]] = []

    # Capture squad scalar attributes before commit
    squad_id_str = f"sq_{squad.id}"
    squad_name = squad.name
    squad_color = squad.color
    squad_icon = squad.icon
    squad_invite_code = squad.invite_code

    for apero in aperos:
        apero_end = apero.ended_at or (apero.created_at + timedelta(hours=4))
        if apero_end.tzinfo is None:
            apero_end = apero_end.replace(tzinfo=timezone.utc)

        if apero.status == AperoStatus.ACTIVE and apero_end <= now:
            apero.status = AperoStatus.ENDED
            if not apero.ended_at:
                apero.ended_at = apero_end

        joined_count = joined_counts.get(apero.id, 0)
        user_status = user_parts.get(apero.id)

        item: Dict[str, Any] = {
            "id": f"bc_{apero.id}",
            "creator_name": apero.creator.username,
            "creator_id": apero.creator_id,
            "location_name": apero.location_name or "Lieu inconnu",
            "longitude": apero.longitude,
            "latitude": apero.latitude,
            "status": apero.status,
            "scheduled_for": force_utc(apero.scheduled_for),
            "started_at": force_utc(apero.started_at),
            "ended_at": force_utc(apero.ended_at),
            "participants_count": joined_count,
            "has_responded": user_status is not None,
            "user_status": user_status,
            "can_start": apero.status == AperoStatus.SCHEDULED,
        }

        if apero.status == AperoStatus.SCHEDULED:
            scheduled_beer_calls.append(item)
        elif apero.status == AperoStatus.ACTIVE:
            active_beer_call.append(item)
        else:
            past_beer_calls.append(item)

    # 5. Commit any expired status changes to the database
    db.commit()

    return {
        "id": squad_id_str,
        "name": squad_name,
        "color": squad_color,
        "icon": squad_icon,
        "invite_code": squad_invite_code,
        "active_beer_call": active_beer_call,
        "scheduled_beer_calls": scheduled_beer_calls,
        "past_beer_calls": past_beer_calls,
    }
