import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from models.websocket_ticket import WebSocketTicket

def generate_ticket_for_squad(db: Session, user_id: int, squad_id: int) -> dict:
    """
    Generates a secure single-use ticket for WebSocket authentication.
    Returns the raw ticket (to be sent to client) and its expiration.
    The database only stores the SHA-256 digest of the ticket.
    """
    # Purge expired tickets
    now = datetime.now(timezone.utc)
    db.query(WebSocketTicket).filter(WebSocketTicket.expires_at < now).delete()

    # Generate 32 bytes urlsafe token
    raw_token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    expires_at = now + timedelta(seconds=60)

    ticket = WebSocketTicket(
        digest=digest,
        user_id=user_id,
        squad_id=squad_id,
        expires_at=expires_at,
    )
    db.add(ticket)
    db.commit()

    return {
        "ticket": raw_token,
        "expires_at": expires_at.isoformat()
    }
