import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app
from datetime import datetime, timedelta, timezone
from jose import jwt
from sqlalchemy.orm import Session
from core.security import ALGORITHM, SECRET_KEY, create_access_token, create_refresh_token
from models.user import User

client = TestClient(app)

class TestAuthAndProfile:
    def test_login_success(self):
        response = client.post('/api/auth/token/', data={'username': 'test@beercall.com', 'password': 'password'})
        assert response.status_code == 401

    def test_get_profile(self):
        response = client.get('/api/auth/me/')
        assert response.status_code == 401


@pytest.fixture
def auth_user(db_session: Session) -> User:
    user = User(username="jwt-user", hashed_password="unused", avatar_config={
        "head": "h_1", "body": "b_1", "legs": "l_1", "feet": "f_1",
        "accessory": None, "animation": "idle", "gender": "male",
    })
    db_session.add(user)
    db_session.commit()
    return user


@pytest.mark.parametrize("kind", ["expired", "invalid"])
def test_rejected_access_token(client: TestClient, auth_user: User, kind: str) -> None:
    token = "invalid.jwt.token"
    if kind == "expired":
        token = jwt.encode({"sub": auth_user.username, "type": "access", "exp": datetime.now(timezone.utc) - timedelta(seconds=60)}, SECRET_KEY, algorithm=ALGORITHM)
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_refresh_cannot_be_used_as_access(client: TestClient, auth_user: User) -> None:
    token = create_refresh_token({"sub": auth_user.username})
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_access_for_unknown_user(client: TestClient) -> None:
    token = create_access_token({"sub": "missing-user"})
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}


def test_valid_refresh_issues_usable_access(client: TestClient, auth_user: User) -> None:
    token = create_refresh_token({"sub": auth_user.username})
    response = client.post("/api/auth/refresh/", data={"refresh_token": token})
    assert response.status_code == 200
    assert set(response.json()) == {"access_token", "refresh_token", "token_type"}
    assert response.json()["token_type"] == "bearer"
    for field, expected_type in (("access_token", "access"), ("refresh_token", "refresh")):
        payload = jwt.decode(response.json()[field], SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == auth_user.username
        assert payload["type"] == expected_type
    profile = client.get("/api/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"})
    assert profile.status_code == 200
    assert profile.json()["username"] == auth_user.username


def test_valid_access_token(client: TestClient, auth_user: User) -> None:
    token = create_access_token({"sub": auth_user.username})
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["username"] == auth_user.username
