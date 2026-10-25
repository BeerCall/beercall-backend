"""Profile read statistics aggregates service.

Computes user profile stats (creations, participations, missed aperos, fraud, prestige score, rank title)
and skin projections using bounded SQL queries without hydrating Apero, AperoParticipant, or Skin ORM entities.
"""
from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple, Union

from sqlalchemy import func, select, union_all, inspect as sa_inspect
from sqlalchemy.orm import Session

from models.apero import Apero, AperoParticipant, ParticipationStatus
from models.gamification import user_skins
from models.user import User


@dataclass(frozen=True)
class ProfileStats:
    aperos_created: int
    aperos_joined: int
    aperos_declined: int
    aperos_missed: int
    fraud_count: int


@dataclass(frozen=True)
class ProfileAggregates:
    score: int
    title: str
    aperos_created: int
    aperos_joined: int
    aperos_declined: int
    aperos_missed: int
    fraud_count: int

    @property
    def stats(self) -> ProfileStats:
        return ProfileStats(
            aperos_created=self.aperos_created,
            aperos_joined=self.aperos_joined,
            aperos_declined=self.aperos_declined,
            aperos_missed=self.aperos_missed,
            fraud_count=self.fraud_count,
        )

    @property
    def stats_dict(self) -> Dict[str, int]:
        return {
            "aperos_created": self.aperos_created,
            "aperos_joined": self.aperos_joined,
            "aperos_declined": self.aperos_declined,
            "aperos_missed": self.aperos_missed,
            "fraud_count": self.fraud_count,
        }


def compute_score(created: int, joined: int, declined: int, fraud: int) -> int:
    """Compute user prestige score according to canonical formula:
    max(0, 100 + 50*created + 30*joined + 5*declined - 15*fraud)
    """
    total = 100 + (created * 50) + (joined * 30) + (declined * 5) - (fraud * 15)
    return max(0, total)


def get_rank_title(score: int) -> str:
    """Canonical prestige rank title based on score thresholds."""
    if score < 50:
        return "Nouvelle Recrue"
    if score < 150:
        return "Amateur de Houblon"
    if score < 300:
        return "Pilier de Comptoir"
    if score < 600:
        return "Maître Brasseur"
    if score < 1000:
        return "Chevalier de la Pinte"
    if score < 2500:
        return "Seigneur du Fût"
    if score < 5000:
        return "Légende de l'Apéro"
    if score < 10000:
        return "Empereur de la Soif"
    return "Dieu du Beer Call"


def get_aperos_created_count(db: Session, user_id: int) -> int:
    """Count aperos created by user in SQL without entity hydration."""
    count = db.query(func.count(Apero.id)).filter(Apero.creator_id == user_id).scalar()
    return count or 0


def get_participation_counts(db: Session, user_id: int) -> Tuple[int, int]:
    """Count JOINED and DECLINED participations for user in SQL without entity hydration.
    Returns (joined_count, declined_count).
    """
    rows = (
        db.query(AperoParticipant.status, func.count(AperoParticipant.id))
        .filter(
            AperoParticipant.user_id == user_id,
            AperoParticipant.status.in_([ParticipationStatus.JOINED, ParticipationStatus.DECLINED]),
        )
        .group_by(AperoParticipant.status)
        .all()
    )
    counts = {status: count for status, count in rows}
    return (
        counts.get(ParticipationStatus.JOINED, 0),
        counts.get(ParticipationStatus.DECLINED, 0),
    )


def get_basic_counts(
    db: Session,
    user_id: int,
    fraud_override: Optional[int] = None,
) -> Tuple[int, int, int, int]:
    """Fetch created, joined, declined, and fraud counts in a single bounded SQL statement.
    Returns (created_count, joined_count, declined_count, fraud_count).
    """
    created_sub = select(func.count(Apero.id)).where(Apero.creator_id == user_id).scalar_subquery()
    joined_sub = select(func.count(AperoParticipant.id)).where(
        AperoParticipant.user_id == user_id,
        AperoParticipant.status == ParticipationStatus.JOINED,
    ).scalar_subquery()
    declined_sub = select(func.count(AperoParticipant.id)).where(
        AperoParticipant.user_id == user_id,
        AperoParticipant.status == ParticipationStatus.DECLINED,
    ).scalar_subquery()

    if fraud_override is not None:
        row = db.execute(select(created_sub, joined_sub, declined_sub)).one()
        return (row[0] or 0, row[1] or 0, row[2] or 0, fraud_override)

    fraud_sub = select(func.coalesce(User.ia_fraud_count, 0)).where(User.id == user_id).scalar_subquery()
    row = db.execute(select(created_sub, joined_sub, declined_sub, fraud_sub)).one()
    return (row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0)


def get_aperos_missed_count(db: Session, user_id: int) -> int:
    """Calculate missed aperos count:
    - Origin date per squad: MIN(created_at) across creations and JOINED participations.
    - An apero counts as missed if apero.created_at >= squad_origin_date AND the user
      has no AperoParticipant row (regardless of status).
    Executed in a single bounded SQL query without ORM hydration.
    """
    q_created = (
        select(
            Apero.squad_id.label("squad_id"),
            func.min(Apero.created_at).label("first_date"),
        )
        .where(Apero.creator_id == user_id)
        .group_by(Apero.squad_id)
    )

    q_joined = (
        select(
            Apero.squad_id.label("squad_id"),
            func.min(Apero.created_at).label("first_date"),
        )
        .join(AperoParticipant, Apero.id == AperoParticipant.apero_id)
        .where(
            AperoParticipant.user_id == user_id,
            AperoParticipant.status == ParticipationStatus.JOINED,
        )
        .group_by(Apero.squad_id)
    )

    activities_union = union_all(q_created, q_joined).subquery("activities")

    squad_origins = (
        select(
            activities_union.c.squad_id.label("squad_id"),
            func.min(activities_union.c.first_date).label("origin_date"),
        )
        .group_by(activities_union.c.squad_id)
        .subquery("squad_origins")
    )

    has_participation = (
        select(1)
        .select_from(AperoParticipant)
        .where(
            AperoParticipant.apero_id == Apero.id,
            AperoParticipant.user_id == user_id,
        )
        .exists()
    )

    stmt = (
        select(func.count(Apero.id))
        .select_from(Apero)
        .join(squad_origins, Apero.squad_id == squad_origins.c.squad_id)
        .where(
            Apero.created_at >= squad_origins.c.origin_date,
            ~has_participation,
        )
    )

    count = db.execute(stmt).scalar()
    return count or 0


def get_profile_aggregates(
    db: Session,
    user_or_id: Union[User, int],
    fraud_count: Optional[int] = None,
) -> ProfileAggregates:
    """Fetch all aggregated read statistics for a user profile in bounded queries
    without hydrating Apero or AperoParticipant ORM collections.
    """
    if isinstance(user_or_id, User):
        insp = sa_inspect(user_or_id)
        if insp is not None and insp.identity:
            user_id = insp.identity[0]
        else:
            user_id = user_or_id.id
    else:
        user_id = user_or_id

    created, joined, declined, fraud = get_basic_counts(db, user_id, fraud_override=fraud_count)
    missed = get_aperos_missed_count(db, user_id)

    score = compute_score(created=created, joined=joined, declined=declined, fraud=fraud)
    title = get_rank_title(score)

    return ProfileAggregates(
        score=score,
        title=title,
        aperos_created=created,
        aperos_joined=joined,
        aperos_declined=declined,
        aperos_missed=missed,
        fraud_count=fraud,
    )


def get_guest_profile_aggregates() -> ProfileAggregates:
    """Aggregates for unauthenticated guest profile."""
    return ProfileAggregates(
        score=0,
        title="Nouveau Venu",
        aperos_created=0,
        aperos_joined=0,
        aperos_declined=0,
        aperos_missed=0,
        fraud_count=0,
    )


def get_owned_skin_ids(db: Session, user_id: int) -> Set[str]:
    """Retrieve the set of skin IDs owned by the user via projection
    from user_skins association table without hydrating Skin entities.
    """
    stmt = select(user_skins.c.skin_id).where(user_skins.c.user_id == user_id)
    rows = db.execute(stmt).scalars().all()
    return set(rows)
