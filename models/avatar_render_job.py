from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, JSON, String, Text, func

from db.database import Base


class AvatarRenderJob(Base):
    """File de rendu des trois sprites d'un avatar, dédupliquée par tenue."""

    __tablename__ = "avatar_render_jobs"

    id = Column(Integer, primary_key=True, index=True)
    config_hash = Column(String(64), nullable=False, unique=True, index=True)
    avatar_config = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default="queued", server_default="queued")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    last_error = Column(Text, nullable=True)
    run_after = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    owner_token = Column(String(36), nullable=True)

    __table_args__ = (
        Index("ix_avatar_render_jobs_status_run_after", "status", "run_after"),
    )
