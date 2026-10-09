import uuid
from sqlalchemy import Column, Integer, JSON, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from db.database import Base

class RealtimeEvent(Base):
    __tablename__ = "realtime_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    squad_id = Column(Integer, ForeignKey("squads.id", ondelete="CASCADE"), nullable=False)
    payload = Column(JSON, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_realtime_events_dispatched_at", "dispatched_at"),
    )
