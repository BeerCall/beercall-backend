"""Contract tests for BeerCall profile read endpoints (/api/auth/profile/ and /api/auth/profile/{user_id}/).

Validates:
- Exact response contract for guest (unauthenticated signup mode)
- Exact response contract for own profile (authenticated, titles, badges, squads, items ownership)
- Exact response contract for other user profile (titles, badges, stats, permissions)
- Accurate stats counting for aperos JOINED, DECLINED, GHOST
- Accurate calculation of missed aperos before/after first activity (creation or JOINED)
- Multi-squad missed calculation and exclusion of any responded status
"""
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from models.user import User
from models.squad import Squad
from models.apero import Apero, AperoParticipant, ParticipationStatus, AperoStatus
from models.gamification import Skin, Badge
from core.security import create_access_token


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


def create_test_user(db: Session, username: str, caps: int = 0) -> User:
    user = User(
        username=username,
        hashed_password="hashed_pw_dummy",
        capsules=caps,
        avatar_config={
            "head": "h_1", "body": "b_1", "legs": "l_1", "feet": "f_1",
            "accessory": None, "animation": "idle", "gender": "male"
        }
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_test_apero(**kwargs) -> Apero:
    defaults = {"latitude": 48.8566, "longitude": 2.3522}
    defaults.update(kwargs)
    return Apero(**defaults)


def auth_headers(user: User) -> dict:
    token = create_access_token({"sub": user.username})
    return {"Authorization": f"Bearer {token}"}


def test_guest_profile_exact_contract(client: TestClient, db_session: Session):
    """Guest profile (/api/auth/profile/ without token) contract for onboarding."""
    response = client.get("/api/auth/profile/")
    assert response.status_code == 200
    data = response.json()

    assert data["id"] == 0
    assert data["username"] == "Guest"
    assert data["caps"] == 0
    assert data["score"] == 0
    assert data["title"] == "Nouveau Venu"
    assert data["avatar"] == {}
    assert data["unlocked_badges"] == []
    assert data["squads"] == []
    assert data["stats"] == {
        "aperos_created": 0,
        "aperos_joined": 0,
        "aperos_declined": 0,
        "aperos_missed": 0,
        "fraud_count": 0
    }

    # All items in shop must have price=0 and is_owned=True for guest avatar builder
    shop_items = data["shop_items"]
    assert isinstance(shop_items, list)
    assert len(shop_items) > 0
    for item in shop_items:
        assert item["price"] == 0
        assert item["is_owned"] is True
        assert set(item.keys()) == {"id", "name", "category", "gender", "price", "is_owned"}


def test_own_profile_exact_contract(client: TestClient, db_session: Session):
    """Own profile (/api/auth/profile/ with token) contract including badges, squads and shop ownership."""
    user = create_test_user(db_session, "own_profile_user", caps=75)

    # 1. Add a squad
    squad = Squad(name="Les Houblons", icon="🍺", color="#ff8800", invite_code="HOUB1234")
    db_session.add(squad)
    db_session.flush()
    squad.members.append(user)

    # 2. Add aperos so that score becomes: 100 (base) + 2*50 (created) + 1*30 (joined) = 230
    now = datetime.now(timezone.utc)
    apero1 = create_test_apero(creator_id=user.id, squad_id=squad.id, status=AperoStatus.ENDED, created_at=now - timedelta(days=2))
    apero2 = create_test_apero(creator_id=user.id, squad_id=squad.id, status=AperoStatus.ENDED, created_at=now - timedelta(days=1))
    apero3 = create_test_apero(creator_id=user.id, squad_id=squad.id, status=AperoStatus.ENDED, created_at=now - timedelta(hours=5))
    db_session.add_all([apero1, apero2, apero3])
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=apero3.id, user_id=user.id, status=ParticipationStatus.JOINED))

    # 3. Add a badge
    badge = db_session.get(Badge, "BAPTEME")
    if not badge:
        badge = Badge(id="BAPTEME", name="Le Baptême", description="1er apéro rejoint", icon="👼")
        db_session.add(badge)
        db_session.flush()
    user.badges.append(badge)

    # 4. Add skin ownership
    skin = db_session.query(Skin).first()
    if not skin:
        skin = Skin(id="Men_head_h1_50", name="h1", category="head", gender="Men", price_caps=50)
        db_session.add(skin)
        db_session.flush()
    user.skins.append(skin)
    db_session.commit()

    headers = auth_headers(user)
    response = client.get("/api/auth/profile/", headers=headers)
    assert response.status_code == 200
    data = response.json()

    assert data["id"] == user.id
    assert data["username"] == "own_profile_user"
    assert data["caps"] == 75
    # score: 100 base + 3*50 (created) + 1*30 (joined) = 280
    assert data["score"] == 280
    # Score 280 -> rank title "Pilier de Comptoir" (200-299)
    assert data["title"] == "Pilier de Comptoir"
    assert data["avatar"] == user.avatar_config

    # Unlocked badges
    badge_ids = [b["id"] for b in data["unlocked_badges"]]
    assert "BAPTEME" in badge_ids

    # Squads list
    squad_ids = [s["id"] for s in data["squads"]]
    assert squad.id in squad_ids

    # Shop items ownership
    owned_items = [item for item in data["shop_items"] if item["id"] == skin.id]
    assert len(owned_items) == 1
    assert owned_items[0]["is_owned"] is True
    assert owned_items[0]["price"] == skin.price_caps

    unowned_items = [item for item in data["shop_items"] if item["id"] != skin.id]
    if unowned_items:
        assert unowned_items[0]["is_owned"] is False


def test_other_user_profile_exact_contract(client: TestClient, db_session: Session):
    """Target user profile (/api/auth/profile/{user_id}/) contract and permissions."""
    viewer = create_test_user(db_session, "viewer_user")
    target = create_test_user(db_session, "target_user", caps=50)

    headers_viewer = auth_headers(viewer)

    # 1. Unauthenticated -> 401
    resp_unauth = client.get(f"/api/auth/profile/u_{target.id}/")
    assert resp_unauth.status_code == 401

    # 2. Authenticated viewer sees target's profile
    resp = client.get(f"/api/auth/profile/u_{target.id}/", headers=headers_viewer)
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == target.id
    assert data["username"] == "target_user"
    assert data["caps"] == 50
    assert data["score"] == 100
    # Score 100 -> rank title "Amateur de Houblon" (50-149)
    assert data["title"] == "Amateur de Houblon"

    # Also supports numeric string without u_ prefix
    resp_raw = client.get(f"/api/auth/profile/{target.id}/", headers=headers_viewer)
    assert resp_raw.status_code == 200
    assert resp_raw.json()["id"] == target.id

    # 3. Nonexistent user -> 404
    resp_not_found = client.get("/api/auth/profile/u_999999/", headers=headers_viewer)
    assert resp_not_found.status_code == 404

    # 4. Invalid ID format -> 400
    resp_invalid = client.get("/api/auth/profile/u_invalid_id/", headers=headers_viewer)
    assert resp_invalid.status_code == 400


def test_profile_stats_participations_joined_declined_ghost(client: TestClient, db_session: Session):
    """Accurate counts of aperos_created, aperos_joined, aperos_declined with ghost exclusion."""
    user = create_test_user(db_session, "stats_user")
    creator = create_test_user(db_session, "creator_other")

    squad = Squad(name="Stats Squad", icon="🍻", color="#00ff00", invite_code="STATS123")
    db_session.add(squad)
    db_session.flush()
    squad.members.extend([user, creator])

    # 1. Apero created by user
    now = datetime.now(timezone.utc)
    apero_created = create_test_apero(
        creator_id=user.id, squad_id=squad.id,
        created_at=now - timedelta(hours=5),
        status=AperoStatus.ENDED
    )
    db_session.add(apero_created)
    db_session.flush()

    # 2. Apero joined by user
    apero_joined = create_test_apero(
        creator_id=creator.id, squad_id=squad.id,
        created_at=now - timedelta(hours=4),
        status=AperoStatus.ENDED
    )
    db_session.add(apero_joined)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=apero_joined.id, user_id=user.id, status=ParticipationStatus.JOINED))

    # 3. Apero declined by user
    apero_declined = create_test_apero(
        creator_id=creator.id, squad_id=squad.id,
        created_at=now - timedelta(hours=3),
        status=AperoStatus.ENDED
    )
    db_session.add(apero_declined)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=apero_declined.id, user_id=user.id, status=ParticipationStatus.DECLINED))

    # 4. Apero where user is ghost (not joined, not declined)
    apero_ghost = create_test_apero(
        creator_id=creator.id, squad_id=squad.id,
        created_at=now - timedelta(hours=2),
        status=AperoStatus.ENDED
    )
    db_session.add(apero_ghost)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=apero_ghost.id, user_id=user.id, status=ParticipationStatus.GHOST))

    db_session.commit()

    headers = auth_headers(user)
    res = client.get("/api/auth/profile/", headers=headers).json()
    stats = res["stats"]

    assert stats["aperos_created"] == 1
    assert stats["aperos_joined"] == 1
    assert stats["aperos_declined"] == 1
    # GHOST does not count towards joined or declined
    assert stats["fraud_count"] == 0


def test_profile_stats_missed_contract(client: TestClient, db_session: Session):
    """Missed aperos calculation contract:
    - 0 before any first activity (creation or JOINED) in a squad
    - After first activity in a squad, aperos without any participation record count as missed
    - Any participation record (JOINED, DECLINED, GHOST) excludes from missed
    - Multi-squad support with distinct start dates
    """
    user = create_test_user(db_session, "missed_user")
    creator = create_test_user(db_session, "squad_creator")

    squad1 = Squad(name="Squad 1", icon="1️⃣", color="#111111", invite_code="SQ1_TEST")
    squad2 = Squad(name="Squad 2", icon="2️⃣", color="#222222", invite_code="SQ2_TEST")
    db_session.add_all([squad1, squad2])
    db_session.flush()
    squad1.members.extend([user, creator])
    squad2.members.extend([user, creator])

    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    # In Squad 1, create an apero at t0 where user has NO participation
    apero_old = create_test_apero(creator_id=creator.id, squad_id=squad1.id, created_at=t0, status=AperoStatus.ENDED)
    db_session.add(apero_old)
    db_session.commit()

    # User has no activity yet in Squad 1 or Squad 2 -> missed must be 0
    assert user.get_aperos_missed_count(db_session) == 0

    # At t1, user joins an apero in Squad 1 (first activity in Squad 1)
    t1 = t0 + timedelta(days=1)
    apero_first_joined = create_test_apero(creator_id=creator.id, squad_id=squad1.id, created_at=t1, status=AperoStatus.ENDED)
    db_session.add(apero_first_joined)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=apero_first_joined.id, user_id=user.id, status=ParticipationStatus.JOINED))
    db_session.commit()

    # After first activity, apero_old (created before t1) still does NOT count as missed
    assert user.get_aperos_missed_count(db_session) == 0

    # At t2, a new apero is created in Squad 1 with NO user participation -> MUST count as missed
    t2 = t1 + timedelta(days=1)
    apero_missed_s1 = create_test_apero(creator_id=creator.id, squad_id=squad1.id, created_at=t2, status=AperoStatus.ENDED)
    db_session.add(apero_missed_s1)
    db_session.commit()

    assert user.get_aperos_missed_count(db_session) == 1

    # At t3, another apero in Squad 1 where user responded DECLINED -> does NOT count as missed
    t3 = t2 + timedelta(days=1)
    apero_declined_s1 = create_test_apero(creator_id=creator.id, squad_id=squad1.id, created_at=t3, status=AperoStatus.ENDED)
    db_session.add(apero_declined_s1)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=apero_declined_s1.id, user_id=user.id, status=ParticipationStatus.DECLINED))
    db_session.commit()

    assert user.get_aperos_missed_count(db_session) == 1

    # At t4, another apero in Squad 1 where user has GHOST participation -> does NOT count as missed
    t4 = t3 + timedelta(days=1)
    apero_ghost_s1 = create_test_apero(creator_id=creator.id, squad_id=squad1.id, created_at=t4, status=AperoStatus.ENDED)
    db_session.add(apero_ghost_s1)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=apero_ghost_s1.id, user_id=user.id, status=ParticipationStatus.GHOST))
    db_session.commit()

    assert user.get_aperos_missed_count(db_session) == 1

    # Now in Squad 2: user creates an apero at t5 (first activity in Squad 2 via creation)
    t5 = t4 + timedelta(days=1)
    apero_created_s2 = create_test_apero(creator_id=user.id, squad_id=squad2.id, created_at=t5, status=AperoStatus.ENDED)
    db_session.add(apero_created_s2)
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=apero_created_s2.id, user_id=user.id, status=ParticipationStatus.JOINED))
    db_session.commit()

    # Still 1 missed total (Squad 1 has 1, Squad 2 has 0)
    assert user.get_aperos_missed_count(db_session) == 1

    # At t6, apero in Squad 2 where user has no participation -> counts as missed in Squad 2
    t6 = t5 + timedelta(days=1)
    apero_missed_s2 = create_test_apero(creator_id=creator.id, squad_id=squad2.id, created_at=t6, status=AperoStatus.ENDED)
    db_session.add(apero_missed_s2)
    db_session.commit()

    # Total missed = 1 (Squad 1) + 1 (Squad 2) = 2
    assert user.get_aperos_missed_count(db_session) == 2

    # Verify endpoint reflects this exact value
    headers = auth_headers(user)
    res = client.get("/api/auth/profile/", headers=headers).json()
    assert res["stats"]["aperos_missed"] == 2


def test_profile_endpoints_do_not_hydrate_apero_entities(client: TestClient, db_session: Session):
    """Profile read endpoints (/api/auth/me, /api/auth/profile/, /api/auth/profile/{id}/)
    must compute statistics and prestige score without loading Apero or AperoParticipant entities into session.
    """
    user = create_test_user(db_session, "hydrate_check_user", caps=80)
    creator = create_test_user(db_session, "hydrate_check_creator", caps=50)
    viewer = create_test_user(db_session, "hydrate_check_viewer", caps=10)

    squad = Squad(name="Hydrate Squad", icon="🍺", color="#333333", invite_code="HYDRATE1")
    db_session.add(squad)
    db_session.flush()
    squad.members.extend([user, creator, viewer])

    now = datetime.now(timezone.utc)
    # Created apero
    a1 = create_test_apero(creator_id=user.id, squad_id=squad.id, created_at=now - timedelta(hours=5), status=AperoStatus.ENDED)
    # Joined apero
    a2 = create_test_apero(creator_id=creator.id, squad_id=squad.id, created_at=now - timedelta(hours=4), status=AperoStatus.ENDED)
    # Declined apero
    a3 = create_test_apero(creator_id=creator.id, squad_id=squad.id, created_at=now - timedelta(hours=3), status=AperoStatus.ENDED)
    # Missed apero
    a4 = create_test_apero(creator_id=creator.id, squad_id=squad.id, created_at=now - timedelta(hours=2), status=AperoStatus.ENDED)

    db_session.add_all([a1, a2, a3, a4])
    db_session.flush()

    p_created = AperoParticipant(apero_id=a1.id, user_id=user.id, status=ParticipationStatus.JOINED)
    p_joined = AperoParticipant(apero_id=a2.id, user_id=user.id, status=ParticipationStatus.JOINED)
    p_declined = AperoParticipant(apero_id=a3.id, user_id=user.id, status=ParticipationStatus.DECLINED)
    db_session.add_all([p_created, p_joined, p_declined])
    db_session.commit()

    headers_user = auth_headers(user)
    headers_viewer = auth_headers(viewer)

    # 1. Test /api/auth/me
    db_session.expunge_all()
    resp_me = client.get("/api/auth/me", headers=headers_user)
    assert resp_me.status_code == 200
    data_me = resp_me.json()
    assert data_me["stats"]["aperos_created"] == 1
    assert data_me["stats"]["aperos_joined"] == 2
    assert data_me["stats"]["aperos_declined"] == 1
    assert data_me["stats"]["aperos_missed"] == 1
    loaded_me = {type(obj) for obj in db_session.identity_map.values()}
    assert Apero not in loaded_me
    assert AperoParticipant not in loaded_me

    # 2. Test /api/auth/profile/
    db_session.expunge_all()
    resp_own = client.get("/api/auth/profile/", headers=headers_user)
    assert resp_own.status_code == 200
    data_own = resp_own.json()
    assert data_own["stats"]["aperos_created"] == 1
    assert data_own["stats"]["aperos_joined"] == 2
    assert data_own["stats"]["aperos_declined"] == 1
    assert data_own["stats"]["aperos_missed"] == 1
    loaded_own = {type(obj) for obj in db_session.identity_map.values()}
    assert Apero not in loaded_own
    assert AperoParticipant not in loaded_own

    # 3. Test /api/auth/profile/{user_id}/
    db_session.expunge_all()
    resp_other = client.get(f"/api/auth/profile/u_{user.id}/", headers=headers_viewer)
    assert resp_other.status_code == 200
    data_other = resp_other.json()
    assert data_other["stats"]["aperos_created"] == 1
    assert data_other["stats"]["aperos_joined"] == 2
    assert data_other["stats"]["aperos_declined"] == 1
    assert data_other["stats"]["aperos_missed"] == 1
    loaded_other = {type(obj) for obj in db_session.identity_map.values()}
    assert Apero not in loaded_other
    assert AperoParticipant not in loaded_other


def test_profile_endpoints_query_count_bounded_independent_of_squads_scale(client: TestClient, db_session: Session):
    """Statement count for /api/auth/me and /api/auth/profile/{id}/ is O(1) and does not scale
    with the number of squads or apero history.
    """
    engine = db_session.get_bind()
    now = datetime.now(timezone.utc)
    creator = create_test_user(db_session, "creator_for_scale", caps=10)
    viewer = create_test_user(db_session, "viewer_for_scale", caps=10)
    headers_viewer = auth_headers(viewer)

    # User A: in 1 squad with 2 aperos
    user_a = create_test_user(db_session, "user_scale_1squad", caps=20)
    squad_a = Squad(name="Squad A1", icon="🍺", color="#111111", invite_code="SQA1_SCALE")
    db_session.add(squad_a)
    db_session.flush()
    squad_a.members.extend([user_a, creator])

    ap1 = create_test_apero(creator_id=user_a.id, squad_id=squad_a.id, created_at=now - timedelta(days=2), status=AperoStatus.ENDED)
    ap2 = create_test_apero(creator_id=creator.id, squad_id=squad_a.id, created_at=now - timedelta(days=1), status=AperoStatus.ENDED)
    db_session.add_all([ap1, ap2])
    db_session.flush()
    db_session.add(AperoParticipant(apero_id=ap2.id, user_id=user_a.id, status=ParticipationStatus.JOINED))
    db_session.commit()

    # User B: in 10 squads with 20 aperos
    user_b = create_test_user(db_session, "user_scale_10squads", caps=20)
    for i in range(10):
        sq = Squad(name=f"Squad B{i}", icon="🍺", color="#222222", invite_code=f"SQB{i}_SCALE")
        db_session.add(sq)
        db_session.flush()
        sq.members.extend([user_b, creator])

        ap_c = create_test_apero(creator_id=user_b.id, squad_id=sq.id, created_at=now - timedelta(days=5, hours=i), status=AperoStatus.ENDED)
        ap_m = create_test_apero(creator_id=creator.id, squad_id=sq.id, created_at=now - timedelta(days=2, hours=i), status=AperoStatus.ENDED)
        db_session.add_all([ap_c, ap_m])
    db_session.commit()

    headers_a = auth_headers(user_a)
    headers_b = auth_headers(user_b)

    # 1. Measure /api/auth/me
    with StatementCounter(engine) as counter_me_a:
        res_me_a = client.get("/api/auth/me", headers=headers_a)
    assert res_me_a.status_code == 200

    with StatementCounter(engine) as counter_me_b:
        res_me_b = client.get("/api/auth/me", headers=headers_b)
    assert res_me_b.status_code == 200

    # Statement counts for /api/auth/me must be identical (exactly O(1), no growth with 10 squads)
    assert counter_me_a.count == counter_me_b.count

    # 2. Measure /api/auth/profile/{user_id}/ (viewer looking at user A vs user B)
    with StatementCounter(engine) as counter_prof_a:
        res_p_a = client.get(f"/api/auth/profile/u_{user_a.id}/", headers=headers_viewer)
    assert res_p_a.status_code == 200

    with StatementCounter(engine) as counter_prof_b:
        res_p_b = client.get(f"/api/auth/profile/u_{user_b.id}/", headers=headers_viewer)
    assert res_p_b.status_code == 200

    # The statement count for user B profile read must be strictly bounded
    # (In legacy, user B would execute >= 10 additional queries just for missed count)
    diff = counter_prof_b.count - counter_prof_a.count
    # Any difference would only come from user_b.squads eager load (if any), but NEVER from aperos/missed N+1 queries!
    assert diff <= 1

