import pytest
from fastapi.testclient import TestClient
from main import app
from db.database import SessionLocal
from models.user import User
from models.squad import Squad
from models.websocket_ticket import WebSocketTicket
from datetime import datetime, timezone, timedelta
import secrets

client = TestClient(app)

def get_auth_headers(user, db_session):
    # on suppose qu'on a un JWT pour l'utilisateur, on peut le mocker ou créer un token.
    # Dans les tests de l'API on utilise core.security.create_access_token.
    from core.security import create_access_token
    token = create_access_token({"sub": user.username})
    return {"Authorization": f"Bearer {token}"}

def test_generate_ticket_member(db_session):
    # Setup
    user = User(username="test_member", hashed_password="pw")
    squad = Squad(name="test_squad", invite_code="CODE1")
    squad.members.append(user)
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()

    headers = get_auth_headers(user, db_session)
    response = client.post(f"/api/squads/{squad.id}/ws-ticket", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "ticket" in data
    assert "expires_at" in data

    # Verify digest only
    ticket = db_session.query(WebSocketTicket).first()
    assert ticket is not None
    assert ticket.digest != data["ticket"]
    
def test_generate_ticket_non_member(db_session):
    user = User(username="test_non_member", hashed_password="pw")
    squad = Squad(name="test_squad_2", invite_code="CODE2")
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()

    headers = get_auth_headers(user, db_session)
    response = client.post(f"/api/squads/{squad.id}/ws-ticket", headers=headers)
    assert response.status_code == 403

def test_generate_ticket_squad_absent(db_session):
    user = User(username="test_absent", hashed_password="pw")
    db_session.add(user)
    db_session.commit()

    headers = get_auth_headers(user, db_session)
    response = client.post("/api/squads/9999/ws-ticket", headers=headers)
    assert response.status_code == 404

def test_ticket_is_stored_as_digest(db_session):
    user = User(username="test_digest", hashed_password="pw")
    squad = Squad(name="test_squad_3", invite_code="CODE3")
    squad.members.append(user)
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()

    headers = get_auth_headers(user, db_session)
    response = client.post(f"/api/squads/{squad.id}/ws-ticket", headers=headers)
    data = response.json()
    
    ticket = db_session.query(WebSocketTicket).filter(WebSocketTicket.user_id == user.id).first()
    assert ticket is not None
    # We verify the digest logic
    import hashlib
    assert ticket.digest == hashlib.sha256(data["ticket"].encode()).hexdigest()
    
def test_expiration_purge(db_session):
    user = User(username="test_purge", hashed_password="pw")
    squad = Squad(name="test_squad_4", invite_code="CODE4")
    squad.members.append(user)
    
    # manual expired ticket
    expired = WebSocketTicket(
        digest="expired", user_id=1, squad_id=1, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()
    
    expired.user_id = user.id
    expired.squad_id = squad.id
    db_session.add(expired)
    db_session.commit()
    
    headers = get_auth_headers(user, db_session)
    response = client.post(f"/api/squads/{squad.id}/ws-ticket", headers=headers)
    assert response.status_code == 200
    
    expired_check = db_session.query(WebSocketTicket).filter(WebSocketTicket.digest == "expired").first()
    assert expired_check is None
