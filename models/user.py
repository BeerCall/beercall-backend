from sqlalchemy import Column, Integer, String, JSON
from sqlalchemy.orm import relationship

from db.database import Base
from models.gamification import user_badges, user_skins
from models.squad import squad_members


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    capsules = Column(Integer, default=100)
    avatar_config = Column(JSON, nullable=True)
    badges = relationship("Badge", secondary=user_badges, backref="users")
    skins = relationship("Skin", secondary=user_skins, backref="users")
    squads = relationship("Squad", secondary=squad_members, back_populates="members")

    ia_fraud_count = Column(Integer, default=0)
    consecutive_joins = Column(Integer, default=0) # Pour le bonus Streak
    consecutive_declines = Column(Integer, default=0) # Pour le badge Casanier
    consecutive_piscine = Column(Integer, default=0) # Pour le badge Nageur