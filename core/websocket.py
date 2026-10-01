from typing import Dict, List
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        # Map: squad_id -> list of active WebSocket connections
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, squad_id: int):
        await websocket.accept()
        if squad_id not in self.active_connections:
            self.active_connections[squad_id] = []
        self.active_connections[squad_id].append(websocket)

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