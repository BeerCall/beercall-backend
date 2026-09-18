import enum
from datetime import timedelta

from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Enum, JSON, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from db.database import Base


class AperoStatus(enum.Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    ENDED = "ended"
    CANCELLED = "cancelled"


class Apero(Base):
    __tablename__ = "aperos"

    id = Column(Integer, primary_key=True, index=True)
    squad_id = Column(Integer, ForeignKey("squads.id"), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    location_name = Column(String, nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    photo_path = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status = Column(Enum(AperoStatus), nullable=False, default=AperoStatus.ACTIVE, index=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    daily_check_processed_at = Column(DateTime(timezone=True), nullable=True, index=True)

    creator = relationship("User", backref="aperos_created")
    squad = relationship("Squad", backref="aperos")
    current_game_id = Column(String, nullable=True)
    current_game_state = Column(JSON, nullable=True)


class ParticipationStatus(enum.Enum):
    JOINED = "joined"
    DECLINED = "declined"
    GHOST = "ghost"


class AperoParticipant(Base):
    __tablename__ = "apero_participants"
    __table_args__ = (UniqueConstraint("apero_id", "user_id", name="uq_apero_participant_user"),)

    id = Column(Integer, primary_key=True, index=True)
    apero_id = Column(Integer, ForeignKey("aperos.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(Enum(ParticipationStatus), default=ParticipationStatus.GHOST)
    excuse = Column(String, nullable=True)
    photo_path = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    apero = relationship("Apero", backref="participants")
    user = relationship("User", backref="participations")
