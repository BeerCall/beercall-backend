from sqlalchemy import Column, Integer, String, JSON
from sqlalchemy.orm import relationship

from db.database import Base
from models.apero import ParticipationStatus
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
    consecutive_joins = Column(Integer, default=0)  # Pour le bonus Streak
    consecutive_declines = Column(Integer, default=0)  # Pour le badge Casanier
    consecutive_piscine = Column(Integer, default=0)  # Pour le badge Nageur

    push_token = Column(String, nullable=True)

    @property
    def score(self) -> int:
        total = 100
        total += self.aperos_created_count * 50
        total += self.aperos_joined_count * 30
        total += self.aperos_declined_count * 5
        total -= (self.ia_fraud_count * 15)
        return max(0, total)

    @property
    def aperos_created_count(self) -> int:
        return len(self.aperos_created)

    @property
    def aperos_joined_count(self) -> int:
        return sum(1 for p in self.participations if p.status == ParticipationStatus.JOINED)

    @property
    def aperos_declined_count(self) -> int:
        return sum(1 for p in self.participations if p.status == ParticipationStatus.DECLINED)

    @property
    def aperos_missed_count(self) -> int:
        # Tous les apéros des squads du joueur
        total_squad_aperos = sum(len(squad.aperos) for squad in self.squads)
        # Moins ceux où il a une participation enregistrée
        return total_squad_aperos - len(self.participations)
