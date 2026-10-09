import pytest
import asyncio
from fastapi.testclient import TestClient
from main import app
from db.database import SessionLocal
from models.user import User
from models.squad import Squad
from models.websocket_ticket import WebSocketTicket
import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from fastapi import WebSocketDisconnect

client = TestClient(app)

def test_websocket_rejects_missing_protocols(db_session):
    user = User(username="ws_test", hashed_password="pw")
    squad = Squad(name="ws_squad", invite_code="WSCODE")
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()
    
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/api/squads/{squad.id}/ws") as websocket:
            pass
    assert e.value.code == 1008

def test_websocket_consumes_ticket_and_accepts(db_session):
    user = User(username="ws_test2", hashed_password="pw")
    squad = Squad(name="ws_squad2", invite_code="WSCODE2")
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()

    raw_token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    ticket = WebSocketTicket(
        digest=digest,
        user_id=user.id,
        squad_id=squad.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60)
    )
    db_session.add(ticket)
    db_session.commit()

    with client.websocket_connect(f"/api/squads/{squad.id}/ws", subprotocols=["beercall", f"ticket.{raw_token}"]) as websocket:
        assert websocket.accepted_subprotocol == "beercall"

    # Ticket should be marked as consumed
    t = db_session.query(WebSocketTicket).filter(WebSocketTicket.digest == digest).first()
    assert t.consumed_at is not None

def test_websocket_rejects_consumed_ticket(db_session):
    user = User(username="ws_test3", hashed_password="pw")
    squad = Squad(name="ws_squad3", invite_code="WSCODE3")
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()

    raw_token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    ticket = WebSocketTicket(
        digest=digest,
        user_id=user.id,
        squad_id=squad.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        consumed_at=datetime.now(timezone.utc) # Already consumed
    )
    db_session.add(ticket)
    db_session.commit()

    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/api/squads/{squad.id}/ws", subprotocols=["beercall", f"ticket.{raw_token}"]) as websocket:
            pass
    assert e.value.code == 1008

@pytest.mark.asyncio
async def test_websocket_concurrent_handshakes():
    import websockets
    import uuid
    import threading
    
    # Actually, it's easier to use anyio or asyncio gathering of testclient using a proper async test client,
    # but httpx/TestClient websocket_connect is synchronous and uses threading under the hood in Starlette.
    # We can spawn threads to hit it concurrently.
    
    # Clear override so each WS connection gets its own DB session
    app.dependency_overrides.clear()
    
    # We need a real DB session for setup. We can fetch it manually.
    db = SessionLocal()
    user = User(username=f"ws_test4_{uuid.uuid4()}", hashed_password="pw")
    squad = Squad(name=f"ws_squad4_{uuid.uuid4()}", invite_code=str(uuid.uuid4())[:8])
    db.add(user)
    db.add(squad)
    db.commit()

    raw_token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    ticket = WebSocketTicket(
        digest=digest,
        user_id=user.id,
        squad_id=squad.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60)
    )
    db.add(ticket)
    db.commit()
    squad_id = squad.id
    db.close()

    results = []
    
    def connect_ws():
        try:
            with client.websocket_connect(f"/api/squads/{squad_id}/ws", subprotocols=["beercall", f"ticket.{raw_token}"]) as ws:
                results.append("accepted")
        except WebSocketDisconnect:
            results.append("rejected")

    t1 = threading.Thread(target=connect_ws)
    t2 = threading.Thread(target=connect_ws)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    # One should be accepted, one rejected
    assert "accepted" in results
    assert "rejected" in results
    assert results.count("accepted") == 1
    assert results.count("rejected") == 1
