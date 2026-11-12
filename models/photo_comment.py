from sqlalchemy import Column, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db.database import Base
from db.types import UTCDateTime


class ProofPhotoComment(Base):
    """Commentaire d'un membre de squad sous la preuve photo d'un participant."""

    __tablename__ = "proof_photo_comments"

    id = Column(Integer, primary_key=True, index=True)
    apero_id = Column(Integer, ForeignKey("aperos.id", ondelete="CASCADE"), nullable=False)
    photo_owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content = Column(String(500), nullable=False)
    created_at = Column(UTCDateTime(), server_default=func.now(), nullable=False)

    author = relationship("User", foreign_keys=[author_id])
    photo_owner = relationship("User", foreign_keys=[photo_owner_id])

    __table_args__ = (
        Index("ix_proof_photo_comments_apero_owner", "apero_id", "photo_owner_id"),
    )
