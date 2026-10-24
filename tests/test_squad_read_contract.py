"""Contract tests for BeerCall squad read endpoint (/api/squads/{squad_id}).

Validates:
- Strict authorization (401 unauthenticated, 403 non-member, 404 nonexistent squad)
- Exact response contract for empty squad (all fields, sq_ prefix, empty lists)
- Exact response contract for apero items (bc_ prefix, creator info, coordinates, participants_count JOINED only, response status)
- Automatic expiration of active aperos on read (>4h or past ended_at) with DB persistence and placement in past_beer_calls
- Exact handling of SCHEDULED (can_start=True), CANCELLED, ENDED, naive UTC dates and descending created_at ordering
"""
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models.user import User
from models.squad import Squad
from models.apero import Apero, AperoParticipant, ParticipationStatus, AperoStatus
from core.security import create_access_token


def create_test_user(db: Session, username: str) -> User:
    user = User(
        username=username,
        hashed_password="hashed_pw_dummy",
        capsules=0,
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


def test_squad_read_auth_and_permissions(client: TestClient, db_session: Session):
    """Permissions: 401 unauthenticated, 404 nonexistent squad, 403 non-member."""
    member = create_test_user(db_session, "squad_member_user")
    outsider = create_test_user(db_session, "squad_outsider_user")

    squad = Squad(name="Perm Squad", icon="🛡️", color="#333333", invite_code="PERM1234")
    db_session.add(squad)
    db_session.flush()
    squad.members.append(member)
    db_session.commit()

    # 1. 401 unauthenticated
    resp_unauth = client.get(f"/api/squads/{squad.id}")
    assert resp_unauth.status_code == 401

    # 2. 404 nonexistent squad
    resp_404 = client.get("/api/squads/999999", headers=auth_headers(member))
    assert resp_404.status_code == 404
    assert resp_404.json()["detail"] == "Squad introuvable"

    # 3. 403 non-member
    resp_403 = client.get(f"/api/squads/{squad.id}", headers=auth_headers(outsider))
    assert resp_403.status_code == 403
    assert resp_403.json()["detail"] == "Tu ne fais pas partie de cette Squad"


def test_empty_squad_read_contract(client: TestClient, db_session: Session):
    """Exact contract for an empty squad: all fields present, sq_ prefix, empty lists."""
    user = create_test_user(db_session, "empty_squad_user")
    squad = Squad(name="La Taverne Vide", icon="🍺", color="#ffaa00", invite_code="TAV12345")
    db_session.add(squad)
    db_session.flush()
    squad.members.append(user)
    db_session.commit()

    resp = client.get(f"/api/squads/{squad.id}", headers=auth_headers(user))
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == f"sq_{squad.id}"
    assert data["name"] == "La Taverne Vide"
    assert data["color"] == "#ffaa00"
    assert data["icon"] == "🍺"
    assert data["invite_code"] == "TAV12345"
    assert data["active_beer_call"] == []
    assert data["scheduled_beer_calls"] == []
    assert data["past_beer_calls"] == []


def test_squad_read_all_fields_exact(client: TestClient, db_session: Session):
    """Exact fields on apero items, participants_count counting only JOINED, and response status."""
    creator = create_test_user(db_session, "field_creator")
    member_joined = create_test_user(db_session, "field_joined")
    member_declined = create_test_user(db_session, "field_declined")
    member_no_resp = create_test_user(db_session, "field_no_resp")

    squad = Squad(name="Fields Squad", icon="📋", color="#123456", invite_code="FIELD123")
    db_session.add(squad)
    db_session.flush()
    squad.members.extend([creator, member_joined, member_declined, member_no_resp])

    now = datetime.now(timezone.utc)
    apero = Apero(
        creator_id=creator.id,
        squad_id=squad.id,
        location_name="Le Café Belge",
        longitude=2.3522,
        latitude=48.8566,
        status=AperoStatus.ACTIVE,
        created_at=now - timedelta(minutes=30),
        started_at=now - timedelta(minutes=25),
    )
    db_session.add(apero)
    db_session.flush()

    # Add participants with distinct statuses
    db_session.add_all([
        AperoParticipant(apero_id=apero.id, user_id=creator.id, status=ParticipationStatus.JOINED),
        AperoParticipant(apero_id=apero.id, user_id=member_joined.id, status=ParticipationStatus.JOINED),
        AperoParticipant(apero_id=apero.id, user_id=member_declined.id, status=ParticipationStatus.DECLINED),
    ])
    db_session.commit()

    # Query as member_joined
    resp = client.get(f"/api/squads/{squad.id}", headers=auth_headers(member_joined))
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["active_beer_call"]) == 1
    item = data["active_beer_call"][0]

    assert item["id"] == f"bc_{apero.id}"
    assert item["creator_name"] == "field_creator"
    assert item["creator_id"] == creator.id
    assert item["location_name"] == "Le Café Belge"
    assert abs(item["longitude"] - 2.3522) < 1e-4
    assert abs(item["latitude"] - 48.8566) < 1e-4
    assert item["status"] == "active"
    assert item["scheduled_for"] is None
    assert item["started_at"] is not None
    assert item["ended_at"] is None
    # participants_count must count only JOINED participants (creator + member_joined = 2)
    assert item["participants_count"] == 2
    assert item["has_responded"] is True
    assert item["user_status"] == "joined"
    assert item["can_start"] is False

    # Query as member_no_resp
    resp_no_resp = client.get(f"/api/squads/{squad.id}", headers=auth_headers(member_no_resp)).json()
    item_no_resp = resp_no_resp["active_beer_call"][0]
    assert item_no_resp["has_responded"] is False
    assert item_no_resp["user_status"] is None


def test_squad_active_lifecycle_expiration_on_read(client: TestClient, db_session: Session):
    """Automatic expiration of active aperos on read:
    - Active apero older than 4h with no ended_at -> transitions to ENDED, sets ended_at, commits DB, moves to past_beer_calls
    - Active apero with past ended_at -> transitions to ENDED, moves to past_beer_calls
    - Active apero younger than 4h -> stays ACTIVE in active_beer_call
    """
    user = create_test_user(db_session, "expire_test_user")
    squad = Squad(name="Expire Squad", icon="⏳", color="#aa0000", invite_code="EXP12345")
    db_session.add(squad)
    db_session.flush()
    squad.members.append(user)

    now = datetime.now(timezone.utc)

    # 1. Old active apero (> 4h ago, no ended_at)
    created_old = now - timedelta(hours=6)
    apero_expired_no_ended = create_test_apero(
        creator_id=user.id,
        squad_id=squad.id,
        location_name="Vieux Bar",
        status=AperoStatus.ACTIVE,
        created_at=created_old,
        ended_at=None
    )

    # 2. Active apero with explicit ended_at in the past
    apero_expired_with_ended = create_test_apero(
        creator_id=user.id,
        squad_id=squad.id,
        location_name="Bar Clos",
        status=AperoStatus.ACTIVE,
        created_at=now - timedelta(hours=2),
        ended_at=now - timedelta(minutes=15)
    )

    # 3. Fresh active apero (< 4h ago, no ended_at)
    apero_fresh = create_test_apero(
        creator_id=user.id,
        squad_id=squad.id,
        location_name="Bar Frais",
        status=AperoStatus.ACTIVE,
        created_at=now - timedelta(minutes=45),
        ended_at=None
    )

    db_session.add_all([apero_expired_no_ended, apero_expired_with_ended, apero_fresh])
    db_session.commit()

    # Trigger GET /api/squads/{squad_id}
    resp = client.get(f"/api/squads/{squad.id}", headers=auth_headers(user))
    assert resp.status_code == 200
    data = resp.json()

    # Only fresh apero is in active_beer_call
    active_ids = [item["id"] for item in data["active_beer_call"]]
    assert active_ids == [f"bc_{apero_fresh.id}"]

    # Expired aperos must be in past_beer_calls
    past_ids = [item["id"] for item in data["past_beer_calls"]]
    assert f"bc_{apero_expired_no_ended.id}" in past_ids
    assert f"bc_{apero_expired_with_ended.id}" in past_ids

    # Verify database state was committed
    db_session.refresh(apero_expired_no_ended)
    assert apero_expired_no_ended.status == AperoStatus.ENDED
    assert apero_expired_no_ended.ended_at is not None
    # ended_at was set to created_at + 4h
    ended_at_val = apero_expired_no_ended.ended_at
    if ended_at_val.tzinfo is None:
        ended_at_val = ended_at_val.replace(tzinfo=timezone.utc)
    expected_ended = created_old + timedelta(hours=4)
    assert abs((ended_at_val - expected_ended).total_seconds()) < 2

    db_session.refresh(apero_expired_with_ended)
    assert apero_expired_with_ended.status == AperoStatus.ENDED

    db_session.refresh(apero_fresh)
    assert apero_fresh.status == AperoStatus.ACTIVE


def test_squad_scheduled_and_cancelled_and_past_ordering(client: TestClient, db_session: Session):
    """Categorization in scheduled_beer_calls, can_start=True, cancelled/ended in past,
    descending created_at ordering and naive UTC dates handling."""
    user = create_test_user(db_session, "ordering_user")
    squad = Squad(name="Order Squad", icon="📅", color="#445566", invite_code="ORD12345")
    db_session.add(squad)
    db_session.flush()
    squad.members.append(user)

    now = datetime.now(timezone.utc)

    # Scheduled apero in the future
    apero_scheduled = create_test_apero(
        creator_id=user.id,
        squad_id=squad.id,
        location_name="Futur Bar",
        status=AperoStatus.SCHEDULED,
        created_at=now - timedelta(days=1),
        scheduled_for=now + timedelta(days=2)
    )

    # Cancelled apero
    apero_cancelled = create_test_apero(
        creator_id=user.id,
        squad_id=squad.id,
        location_name="Bar Annulé",
        status=AperoStatus.CANCELLED,
        created_at=now - timedelta(days=2)
    )

    # Two ended aperos with distinct created_at to verify descending order
    # One with naive datetime in DB
    naive_dt = (now - timedelta(days=3)).replace(tzinfo=None)
    apero_ended_1 = create_test_apero(
        creator_id=user.id,
        squad_id=squad.id,
        location_name="Bar Passé Récent",
        status=AperoStatus.ENDED,
        created_at=now - timedelta(days=3),
        ended_at=now - timedelta(days=3, hours=-2)
    )
    apero_ended_2 = create_test_apero(
        creator_id=user.id,
        squad_id=squad.id,
        location_name="Bar Passé Ancien",
        status=AperoStatus.ENDED,
        created_at=now - timedelta(days=5),
        ended_at=now - timedelta(days=5, hours=-2)
    )

    db_session.add_all([apero_scheduled, apero_cancelled, apero_ended_1, apero_ended_2])
    db_session.commit()

    resp = client.get(f"/api/squads/{squad.id}", headers=auth_headers(user))
    assert resp.status_code == 200
    data = resp.json()

    # Scheduled
    assert len(data["scheduled_beer_calls"]) == 1
    sched_item = data["scheduled_beer_calls"][0]
    assert sched_item["id"] == f"bc_{apero_scheduled.id}"
    assert sched_item["can_start"] is True
    assert sched_item["scheduled_for"] is not None

    # Past beer calls must contain cancelled + ended, ordered desc by created_at
    past_items = data["past_beer_calls"]
    assert len(past_items) == 3
    # Check descending order by created_at: cancelled (days=2) -> ended_1 (days=3) -> ended_2 (days=5)
    past_ids = [item["id"] for item in past_items]
    assert past_ids == [f"bc_{apero_cancelled.id}", f"bc_{apero_ended_1.id}", f"bc_{apero_ended_2.id}"]

    for past_item in past_items:
        assert past_item["can_start"] is False
