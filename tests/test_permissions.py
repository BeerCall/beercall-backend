import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from core.security import create_access_token
from models.apero import Apero, AperoStatus
from models.beer_call_job import BeerCallJob
from models.squad import Squad
from models.user import User


@pytest.fixture
def permission_context(db_session: Session) -> tuple[Squad, Apero, dict[str, str], dict[str, str]]:
    member = User(username="member", hashed_password="unused")
    outsider = User(username="outsider", hashed_password="unused")
    squad = Squad(name="Private squad", icon="beer", color="#123456", invite_code="PERM1234", members=[member])
    db_session.add_all([squad, outsider])
    db_session.flush()
    apero = Apero(squad_id=squad.id, creator_id=member.id, status=AperoStatus.ACTIVE, latitude=48.8, longitude=2.3)
    db_session.add(apero)
    db_session.commit()
    headers = lambda user: {"Authorization": f"Bearer {create_access_token({'sub': user.username})}"}
    return squad, apero, headers(member), headers(outsider)


@pytest.mark.parametrize("endpoint", ["details", "create", "worlds"])
def test_nonmember_is_forbidden(client: TestClient, db_session: Session, permission_context, endpoint: str) -> None:
    squad, apero, _, headers = permission_context
    if endpoint == "details":
        response = client.get(f"/api/squads/{squad.id}", headers=headers)
    elif endpoint == "create":
        response = client.post(
            f"/api/squads/{squad.id}/beer-calls/",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            data={"latitude": 48.8, "longitude": 2.3, "location_name": "Bar"},
            files={"file": ("photo.jpg", b"\xff\xd8\xffphoto", "image/jpeg")},
        )
    else:
        response = client.get(f"/api/squads/{squad.id}/beer-calls/bc_{apero.id}/worlds", headers=headers)
    assert response.status_code == 403
    expected = "Accès refusé." if endpoint == "worlds" else "Tu ne fais pas partie de cette Squad"
    assert response.json() == {"detail": expected}
    assert db_session.query(BeerCallJob).count() == 0
    assert db_session.query(Apero).count() == 1


def test_missing_squad_is_not_found(client: TestClient, permission_context) -> None:
    squad, _, headers, _ = permission_context
    response = client.get(f"/api/squads/{squad.id + 1}", headers=headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Squad introuvable"}


def test_member_can_read_squad_and_worlds(client: TestClient, permission_context) -> None:
    squad, apero, headers, _ = permission_context
    response = client.get(f"/api/squads/{squad.id}", headers=headers)
    assert response.status_code == 200
    response = client.get(f"/api/squads/{squad.id}/beer-calls/bc_{apero.id}/worlds", headers=headers)
    assert response.status_code == 200
