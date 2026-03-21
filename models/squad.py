from sqlalchemy import Column, Integer, String, Table, ForeignKey
from sqlalchemy.orm import relationship
from db.database import Base

# Table d'association Many-to-Many
squad_members = Table(
    "squad_members",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("squad_id", Integer, ForeignKey("squads.id"), primary_key=True)
)


class Squad(Base):
    __tablename__ = "squads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    icon = Column(String, default="beer")  # ex: "pizza", "beer", "wine"
    color = Column(String, default="#FFCC00")
    invite_code = Column(String, unique=True, index=True)  # Pour le "Rejoindre Squad" du GDD

    # Relations
    members = relationship("User", secondary=squad_members, back_populates="squads")
