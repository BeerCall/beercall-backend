import uuid
from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Index, UUID
from sqlalchemy.sql import func
from db.database import Base

class WebSocketTicket(Base):
    __tablename__ = "websocket_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    digest = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    squad_id = Column(Integer, ForeignKey("squads.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_websocket_tickets_squad_expires", "squad_id", "expires_at"),
    )
