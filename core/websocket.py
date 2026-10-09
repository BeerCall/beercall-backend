from typing import Dict, List
from fastapi import WebSocket
from sqlalchemy.orm import Session
import hashlib
from datetime import datetime, timezone
from models.websocket_ticket import WebSocketTicket

class ConnectionManager:
    def __init__(self):
        # Map: squad_id -> list of active WebSocket connections
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, squad_id: int, db: Session) -> bool:
        protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
        
        ticket_raw = None
        has_beercall = False
        for p in protocols:
            p = p.strip()
            if p == "beercall":
                has_beercall = True
            elif p.startswith("ticket."):
                ticket_raw = p[len("ticket."):]

        if not has_beercall or not ticket_raw:
            await websocket.close(code=1008)
            return False

        digest = hashlib.sha256(ticket_raw.encode("utf-8")).hexdigest()
        
        try:
            now = datetime.now(timezone.utc)
            # Select for update to prevent concurrent consumptions
            ticket = db.query(WebSocketTicket).with_for_update().filter(
                WebSocketTicket.digest == digest,
                WebSocketTicket.squad_id == squad_id
            ).first()

            if not ticket or ticket.consumed_at or ticket.expires_at < now:
                db.rollback()
                await websocket.close(code=1008)
                return False

            ticket.consumed_at = now
            db.commit()
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            await websocket.close(code=1008)
            return False

        await websocket.accept(subprotocol="beercall")
        if squad_id not in self.active_connections:
            self.active_connections[squad_id] = []
        self.active_connections[squad_id].append(websocket)
        return True

    def disconnect(self, websocket: WebSocket, squad_id: int):
        if squad_id in self.active_connections:
            self.active_connections[squad_id].remove(websocket)
            if not self.active_connections[squad_id]:
                del self.active_connections[squad_id]

    async def broadcast_to_squad(self, squad_id: int, message: dict):
        if squad_id in self.active_connections:
            dead_connections = []
            for connection in self.active_connections[squad_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    dead_connections.append(connection)
            for dead in dead_connections:
                self.disconnect(dead, squad_id)

manager = ConnectionManager()
