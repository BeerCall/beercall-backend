import enum

from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from db.database import Base


class Apero(Base):
    __tablename__ = "aperos"

    id = Column(Integer, primary_key=True, index=True)
    squad_id = Column(Integer, ForeignKey("squads.id"), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    location_name = Column(String, nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    photo_path = Column(String, nullable=False)  # Où est stockée l'image
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations pour naviguer facilement dans le code
    creator = relationship("User")
    squad = relationship("Squad")


class ParticipationStatus(enum.Enum):
    JOINED = "joined"  # Le Bar
    DECLINED = "declined"  # La Piscine
    GHOST = "ghost"  # Le Dodo (par défaut)


class AperoParticipant(Base):
    __tablename__ = "apero_participants"

    id = Column(Integer, primary_key=True, index=True)
    apero_id = Column(Integer, ForeignKey("aperos.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(Enum(ParticipationStatus), default=ParticipationStatus.GHOST)
    excuse = Column(String, nullable=True)  # Pour la "Piscine"
    photo_path = Column(String, nullable=True)  # Pour le "Bar"

    apero = relationship("Apero", backref="participants")
    user = relationship("User")
