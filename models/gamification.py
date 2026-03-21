# Fichier : models/gamification.py
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Table
from sqlalchemy.sql import func
from db.database import Base

# --- TABLES D'ASSOCIATION (MANY-TO-MANY) ---

user_badges = Table(
    "user_badges",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("badge_id", String, ForeignKey("badges.id"), primary_key=True),
    Column("unlocked_at", DateTime(timezone=True), server_default=func.now())
)

user_skins = Table(
    "user_skins",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("skin_id", String, ForeignKey("skins.id"), primary_key=True),
    Column("unlocked_at", DateTime(timezone=True), server_default=func.now())
)


# --- TABLES DE RÉFÉRENCE (LE CATALOGUE) ---

class Badge(Base):
    __tablename__ = "badges"

    id = Column(String, primary_key=True, index=True)  # ex: "BAPTÊME", "MARMOTTE"
    name = Column(String, nullable=False)  # ex: "Le Baptême"
    description = Column(String, nullable=True)  # ex: "1er apéro rejoint"
    icon = Column(String, nullable=True)  # ex: "🍺"


class Skin(Base):
    __tablename__ = "skins"

    id = Column(String, primary_key=True, index=True)  # ex: "Horse_Head_Men"
    name = Column(String, nullable=False)  # ex: "Tête de Cheval"
    gender = Column(String, nullable=False)  # ex: "Tête de Cheval"
    category = Column(String, nullable=False)  # ex: "head", "body", "accessory"
    price_caps = Column(Integer, default=0)  # Prix en capsules
