import uuid
from sqlalchemy import Column, String, Integer, Float, ForeignKey, DateTime, Index, UniqueConstraint, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from db.database import Base
import enum

class BeerCallJobStatus(enum.Enum):
    UPLOADING = "uploading"
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"

class BeerCallJob(Base):
    __tablename__ = "beer_call_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    squad_id = Column(Integer, ForeignKey("squads.id", ondelete="CASCADE"), nullable=False)
    
    idempotency_key = Column(String(36), nullable=False)
    
    status = Column(Enum(BeerCallJobStatus), default=BeerCallJobStatus.PENDING, nullable=False)
    
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    location_name = Column(String, nullable=True)
    
    # Used for lease worker locking
    owner_id = Column(String(36), nullable=True)
    
    attempts = Column(Integer, default=0, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("creator_id", "idempotency_key", name="uix_beer_call_job_creator_idempotency"),
    )
