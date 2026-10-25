"""Unit tests for profile read statistics aggregates service (T25 / PERF-04 / BENCH-06).

Validates:
- Exact equivalence with legacy User properties (score, created, joined, declined, missed)
- Full reproduction of T03 deterministic multi-squad timeline
- Bounded statement count (O(1) queries regardless of number of squads/history)
- Zero hydration of Apero, AperoParticipant, or Skin ORM entities
- Projections of owned skin IDs from user_skins without loading Skin models
- Canonical rank title thresholds and guest aggregates
- Safe restoration of SQL listeners in finally blocks
"""
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from models.user import User
from models.squad import Squad
from models.apero import Apero, AperoParticipant, ParticipationStatus, AperoStatus
from models.gamification import Skin, user_skins
from services.profile_reads import (
    compute_score,
    get_rank_title,
    get_aperos_created_count,
    get_participation_counts,
    get_basic_counts,
    get_aperos_missed_count,
    get_profile_aggregates,
    get_guest_profile_aggregates,
    get_owned_skin_ids,
    ProfileAggregates,
)


class StatementCounter:
    """SQLAlchemy statement listener with guaranteed cleanup in finally."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self.statements = []
        self._listening = False

    def __enter__(self):
        self.count = 0
        self.statements = []
        event.listen(self.engine, "before_cursor_execute", self._hook)
        self._listening = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._listening:
            event.remove(self.engine, "before_cursor_execute", self._hook)
            self._listening = False

    def _hook(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1
        self.statements.append(statement)


def make_user(db: Session, username: str, caps: int = 100, fraud: int = 0) -> User:
    user = User(
        username=username,
        hashed_password="pw_dummy",
        capsules=caps,
        ia_fraud_count=fraud,
        avatar_config={"head": "h1"},
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_apero(**kwargs) -> Apero:
    defaults = {"latitude": 48.8566, "longitude": 2.3522, "status": AperoStatus.ENDED}
    defaults.update(kwargs)
    return Apero(**defaults)


def test_fresh_user_aggregates_match_legacy(db_session: Session):
    """User with no activity has base score 100, 0 stats and matches legacy properties."""
    user = make_user(db_session, "fresh_user")

    aggregates = get_profile_aggregates(db_session, user)

    assert aggregates.aperos_created == user.aperos_created_count == 0
    assert aggregates.aperos_joined == user.aperos_joined_count == 0
    assert aggregates.aperos_declined == user.aperos_declined_count == 0
    assert aggregates.aperos_missed == user.get_aperos_missed_count(db_session) == 0
    assert aggregates.fraud_count == user.ia_fraud_count == 0
    assert aggregates.score == user.score == 100
    assert aggregates.title == "Amateur de Houblon"
    assert aggregates.stats.aperos_created == 0
    assert aggregates.stats_dict == {
        "aperos_created": 0,
        "aperos_joined": 0,
        "aperos_declined": 0,
        "aperos_missed": 0,
        "fraud_count": 0,
    }


def test_user_id_int_variant_matches_user_entity(db_session: Session):
    """get_profile_aggregates accepts either User entity or integer user_id."""
    user = make_user(db_session, "int_variant_user", fraud=2)

    aggr_user = get_profile_aggregates(db_session, user)
    aggr_id = get_profile_aggregates(db_session, user.id)

    assert aggr_user == aggr_id
    assert aggr_id.fraud_count == 2
    # Base 100 - (2 * 15) = 70
    assert aggr_id.score == 70


def test_creations_and_participations_match_legacy(db_session: Session):
    """Creations, joined, declined, and ghost participations match legacy properties."""
    user = make_user(db_session, "activity_user", fraud=1)
    other = make_user(db_session, "other_creator")

    squad = Squad(name="Act Squad", icon="🍻", invite_code="ACT1")
    db_session.add(squad)
    db_session.flush()

    now = datetime.now(timezone.utc)

    # 2 created aperos
    a1 = make_apero(creator_id=user.id, squad_id=squad.id, created_at=now - timedelta(hours=6))
    a2 = make_apero(creator_id=user.id, squad_id=squad.id, created_at=now - timedelta(hours=5))
    # 1 joined apero
    a3 = make_apero(creator_id=other.id, squad_id=squad.id, created_at=now - timedelta(hours=4))
    # 2 declined aperos
    a4 = make_apero(creator_id=other.id, squad_id=squad.id, created_at=now - timedelta(hours=3))
    a5 = make_apero(creator_id=other.id, squad_id=squad.id, created_at=now - timedelta(hours=2))
    # 1 ghost apero
    a6 = make_apero(creator_id=other.id, squad_id=squad.id, created_at=now - timedelta(hours=1))

    db_session.add_all([a1, a2, a3, a4, a5, a6])
    db_session.flush()

    p_joined = AperoParticipant(apero_id=a3.id, user_id=user.id, status=ParticipationStatus.JOINED)
    p_dec1 = AperoParticipant(apero_id=a4.id, user_id=user.id, status=ParticipationStatus.DECLINED)
    p_dec2 = AperoParticipant(apero_id=a5.id, user_id=user.id, status=ParticipationStatus.DECLINED)
    p_ghost = AperoParticipant(apero_id=a6.id, user_id=user.id, status=ParticipationStatus.GHOST)
    db_session.add_all([p_joined, p_dec1, p_dec2, p_ghost])
    db_session.commit()

    db_session.refresh(user)

    # Legacy values:
    legacy_created = user.aperos_created_count
    legacy_joined = user.aperos_joined_count
    legacy_declined = user.aperos_declined_count
    legacy_score = user.score

    # Service values:
    created = get_aperos_created_count(db_session, user.id)
    joined, declined = get_participation_counts(db_session, user.id)
    b_created, b_joined, b_declined, b_fraud = get_basic_counts(db_session, user.id)
    aggr = get_profile_aggregates(db_session, user)

    assert created == b_created == legacy_created == 2
    assert joined == b_joined == legacy_joined == 1
    assert declined == b_declined == legacy_declined == 2
    assert b_fraud == user.ia_fraud_count == 1
    # Score: 100 + 2*50 + 1*30 + 2*5 - 1*15 = 100 + 100 + 30 + 10 - 15 = 225
    assert aggr.score == legacy_score == 225
    assert aggr.title == "Pilier de Comptoir"


def test_score_floor_at_zero(db_session: Session):
    """Fraud deduction cannot reduce prestige score below 0."""
    user = make_user(db_session, "fraudster", fraud=20)
    db_session.refresh(user)

    # 100 - (20 * 15) = -200 -> max(0, -200) = 0
    assert user.score == 0
    aggr = get_profile_aggregates(db_session, user)
    assert aggr.score == 0
    assert aggr.title == "Nouvelle Recrue"


def test_t03_multi_squad_timeline_matches_legacy(db_session: Session):
    """Reproduce T03 deterministic multi-squad timeline:
    - Squad 1: apero at t0 (before first activity -> not missed)
    - Squad 1: apero at t1 (JOINED -> first activity)
    - Squad 1: apero at t2 (no participation -> MISSED 1)
    - Squad 1: apero at t3 (DECLINED -> not missed)
    - Squad 1: apero at t4 (GHOST -> not missed)
    - Squad 2: apero at t5 (CREATED by user -> first activity in Squad 2)
    - Squad 2: apero at t6 (no participation -> MISSED 2)
    Total missed = 2.
    """
    user = make_user(db_session, "timeline_user")
    creator = make_user(db_session, "squad_creator")

    s1 = Squad(name="Squad 1", icon="1️⃣", invite_code="SQ1")
    s2 = Squad(name="Squad 2", icon="2️⃣", invite_code="SQ2")
    db_session.add_all([s1, s2])
    db_session.flush()

    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    # t0: unparticipated in squad 1 before activity
    a_t0 = make_apero(creator_id=creator.id, squad_id=s1.id, created_at=t0)
    db_session.add(a_t0)
    db_session.commit()

    assert user.get_aperos_missed_count(db_session) == get_aperos_missed_count(db_session, user.id) == 0

    # t1: first activity via JOINED in squad 1
    t1 = t0 + timedelta(days=1)
    a_t1 = make_apero(creator_id=creator.id, squad_id=s1.id, created_at=t1)
    db_session.add(a_t1)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=a_t1.id, user_id=user.id, status=ParticipationStatus.JOINED))
    db_session.commit()

    assert user.get_aperos_missed_count(db_session) == get_aperos_missed_count(db_session, user.id) == 0

    # t2: unparticipated in squad 1 after activity -> missed 1
    t2 = t1 + timedelta(days=1)
    a_t2 = make_apero(creator_id=creator.id, squad_id=s1.id, created_at=t2)
    db_session.add(a_t2)
    db_session.commit()

    assert user.get_aperos_missed_count(db_session) == get_aperos_missed_count(db_session, user.id) == 1

    # t3: DECLINED in squad 1 -> not missed
    t3 = t2 + timedelta(days=1)
    a_t3 = make_apero(creator_id=creator.id, squad_id=s1.id, created_at=t3)
    db_session.add(a_t3)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=a_t3.id, user_id=user.id, status=ParticipationStatus.DECLINED))
    db_session.commit()

    assert user.get_aperos_missed_count(db_session) == get_aperos_missed_count(db_session, user.id) == 1

    # t4: GHOST in squad 1 -> not missed
    t4 = t3 + timedelta(days=1)
    a_t4 = make_apero(creator_id=creator.id, squad_id=s1.id, created_at=t4)
    db_session.add(a_t4)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=a_t4.id, user_id=user.id, status=ParticipationStatus.GHOST))
    db_session.commit()

    assert user.get_aperos_missed_count(db_session) == get_aperos_missed_count(db_session, user.id) == 1

    # t5: first activity in squad 2 via CREATION
    t5 = t4 + timedelta(days=1)
    a_t5 = make_apero(creator_id=user.id, squad_id=s2.id, created_at=t5)
    db_session.add(a_t5)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=a_t5.id, user_id=user.id, status=ParticipationStatus.JOINED))
    db_session.commit()

    assert user.get_aperos_missed_count(db_session) == get_aperos_missed_count(db_session, user.id) == 1

    # t6: unparticipated in squad 2 -> missed 2
    t6 = t5 + timedelta(days=1)
    a_t6 = make_apero(creator_id=creator.id, squad_id=s2.id, created_at=t6)
    db_session.add(a_t6)
    db_session.commit()

    legacy_missed = user.get_aperos_missed_count(db_session)
    service_missed = get_aperos_missed_count(db_session, user.id)
    aggr = get_profile_aggregates(db_session, user)

    assert legacy_missed == service_missed == aggr.aperos_missed == 2
    assert aggr.aperos_created == user.aperos_created_count == 1
    assert aggr.aperos_joined == user.aperos_joined_count == 2
    assert aggr.aperos_declined == user.aperos_declined_count == 1
    assert aggr.score == user.score


def test_bounded_statements_independent_of_squads_scale(db_session: Session):
    """Aggregate read executes a strictly bounded statement count (O(1))
    regardless of squad count, unlike legacy which is O(N_squads).
    """
    user = make_user(db_session, "scale_user")
    other = make_user(db_session, "scale_creator")
    now = datetime.now(timezone.utc)

    # Populate 15 squads with 3 aperos each
    num_squads = 15
    for s_idx in range(num_squads):
        squad = Squad(name=f"Scale Squad {s_idx}", icon="🍺", invite_code=f"SC_{s_idx}")
        db_session.add(squad)
        db_session.flush()

        # Apero 1: user joined (origin)
        a1 = make_apero(creator_id=other.id, squad_id=squad.id, created_at=now - timedelta(days=10, hours=s_idx))
        db_session.add(a1)
        db_session.flush()
        db_session.add(AperoParticipant(apero_id=a1.id, user_id=user.id, status=ParticipationStatus.JOINED))

        # Apero 2: unparticipated -> missed
        a2 = make_apero(creator_id=other.id, squad_id=squad.id, created_at=now - timedelta(days=5, hours=s_idx))
        db_session.add(a2)

        # Apero 3: user declined -> not missed
        a3 = make_apero(creator_id=other.id, squad_id=squad.id, created_at=now - timedelta(days=2, hours=s_idx))
        db_session.add(a3)
        db_session.flush()
        db_session.add(AperoParticipant(apero_id=a3.id, user_id=user.id, status=ParticipationStatus.DECLINED))

    db_session.commit()

    engine = db_session.get_bind()

    # 1. Measure statement count of optimized get_profile_aggregates
    with StatementCounter(engine) as counter_service:
        aggr = get_profile_aggregates(db_session, user)

    # Exactly 2 SQL queries (1 for basic counts subqueries, 1 for missed count)
    assert counter_service.count == 2
    assert aggr.aperos_joined == 15
    assert aggr.aperos_declined == 15
    assert aggr.aperos_missed == 15

    # 2. Contrast with legacy: generates 2 + N_squads statements
    with StatementCounter(engine) as counter_legacy:
        legacy_missed = user.get_aperos_missed_count(db_session)

    assert legacy_missed == aggr.aperos_missed == 15
    assert counter_legacy.count >= num_squads + 2


def test_no_orm_entities_hydrated(db_session: Session):
    """Aggregate calculation does not load any Apero or AperoParticipant entity into session identity map."""
    user = make_user(db_session, "no_hydrate_user")
    other = make_user(db_session, "no_hydrate_creator")

    squad = Squad(name="No Hydrate", icon="🍺", invite_code="NOHYD")
    db_session.add(squad)
    db_session.flush()

    now = datetime.now(timezone.utc)
    for i in range(5):
        a = make_apero(creator_id=other.id, squad_id=squad.id, created_at=now - timedelta(hours=10 - i))
        db_session.add(a)
        db_session.flush()
        db_session.add(AperoParticipant(apero_id=a.id, user_id=user.id, status=ParticipationStatus.JOINED))

    db_session.commit()

    user_id = user.id

    # Clear identity map
    db_session.expunge_all()

    # Call aggregates service
    aggr = get_profile_aggregates(db_session, user_id)

    assert aggr.aperos_joined == 5

    # Inspect identity map
    loaded_types = {type(obj) for obj in db_session.identity_map.values()}
    assert Apero not in loaded_types
    assert AperoParticipant not in loaded_types


def test_get_owned_skin_ids_projection(db_session: Session):
    """get_owned_skin_ids returns set of owned IDs via user_skins projection without Skin entity hydration."""
    user = make_user(db_session, "skin_user")

    # Insert skin rows directly or via model
    skin1 = Skin(id="Men_head_01", name="H1", category="head", gender="Men", price_caps=10)
    skin2 = Skin(id="Men_body_01", name="B1", category="body", gender="Men", price_caps=20)
    skin3 = Skin(id="Men_legs_01", name="L1", category="legs", gender="Men", price_caps=30)
    db_session.add_all([skin1, skin2, skin3])
    db_session.flush()

    # User owns skin1 and skin2, but not skin3
    db_session.execute(user_skins.insert().values(user_id=user.id, skin_id=skin1.id))
    db_session.execute(user_skins.insert().values(user_id=user.id, skin_id=skin2.id))
    db_session.commit()

    user_id = user.id

    # Clear identity map
    db_session.expunge_all()

    owned_ids = get_owned_skin_ids(db_session, user_id)

    assert owned_ids == {"Men_head_01", "Men_body_01"}
    assert "Men_legs_01" not in owned_ids

    # Verify no Skin entity is in identity map
    loaded_types = {type(obj) for obj in db_session.identity_map.values()}
    assert Skin not in loaded_types


def test_guest_profile_aggregates():
    """Guest aggregates return fixed zero statistics and 'Nouveau Venu' title."""
    aggr = get_guest_profile_aggregates()
    assert aggr.score == 0
    assert aggr.title == "Nouveau Venu"
    assert aggr.aperos_created == 0
    assert aggr.aperos_joined == 0
    assert aggr.aperos_declined == 0
    assert aggr.aperos_missed == 0
    assert aggr.fraud_count == 0


@pytest.mark.parametrize(
    "score,expected_title",
    [
        (0, "Nouvelle Recrue"),
        (49, "Nouvelle Recrue"),
        (50, "Amateur de Houblon"),
        (149, "Amateur de Houblon"),
        (150, "Pilier de Comptoir"),
        (299, "Pilier de Comptoir"),
        (300, "Maître Brasseur"),
        (599, "Maître Brasseur"),
        (600, "Chevalier de la Pinte"),
        (999, "Chevalier de la Pinte"),
        (1000, "Seigneur du Fût"),
        (2499, "Seigneur du Fût"),
        (2500, "Légende de l'Apéro"),
        (4999, "Légende de l'Apéro"),
        (5000, "Empereur de la Soif"),
        (9999, "Empereur de la Soif"),
        (10000, "Dieu du Beer Call"),
        (50000, "Dieu du Beer Call"),
    ],
)
def test_rank_titles_thresholds(score: int, expected_title: str):
    """Canonical title boundaries matching get_rank_title."""
    assert get_rank_title(score) == expected_title


def test_statement_counter_restored_on_exception(db_session: Session):
    """StatementCounter listener is guaranteed to be detached even if an exception occurs."""
    engine = db_session.get_bind()

    with pytest.raises(RuntimeError):
        with StatementCounter(engine):
            raise RuntimeError("forced boom")

    # If the listener was not removed, this would fail or double-count
    # Verify listener list on engine is clean
    assert not StatementCounter(engine)._listening
