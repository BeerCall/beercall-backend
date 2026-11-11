import os
import pytest
from alembic.config import Config
from alembic import command
from sqlalchemy import text

@pytest.fixture
def alembic_config(postgres_url):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    return config

def test_empty_to_head_and_downgrade(alembic_config, postgres_engine):
    # Ensure starting clean
    with postgres_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()

    # empty -> head
    command.upgrade(alembic_config, "head")


    with postgres_engine.connect() as conn:
        # Check tables
        tables = conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )).scalars().all()
        assert "users" in tables
        assert "apero_participants" in tables
        assert "aperos" in tables
        assert "squads" in tables
        assert "alembic_version" in tables
        assert "proof_photo_comments" in tables
        comment_indexes = conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'proof_photo_comments'"
        )).scalars().all()
        assert "ix_proof_photo_comments_apero_owner" in comment_indexes
        timestamp_type = conn.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'proof_photo_comments' AND column_name = 'created_at'"
        )).scalar_one()
        assert timestamp_type == "timestamp with time zone"
        cascading_keys = conn.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = 'proof_photo_comments'::regclass "
            "AND contype = 'f' AND confdeltype = 'c'"
        )).scalar_one()
        assert cascading_keys == 3

        # Vérifier l'unique participant
        constraints = conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'apero_participants'::regclass"
        )).scalars().all()
        assert any("uq_" in c for c in constraints) or any("unique" in c.lower() for c in constraints) or any("apero_participants_apero_id_user_id_key" in c for c in constraints)

        # Vérifier index lifecycle (sur aperos, prob status)
        indexes = conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'aperos'"
        )).scalars().all()
        assert any("status" in i for i in indexes) or any("ix_aperos_status" in i for i in indexes)

    # head -> base -> head
    command.downgrade(alembic_config, "base")
    
    with postgres_engine.connect() as conn:
        tables = conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )).scalars().all()
        # Should only have alembic_version
        assert "users" not in tables
        assert "proof_photo_comments" not in tables
        assert "alembic_version" in tables
        
    command.upgrade(alembic_config, "head")


def test_plan_b_preserves_existing_user_timestamp(alembic_config, postgres_engine):
    from datetime import datetime, timezone
    command.downgrade(alembic_config, "9c4e6f2a1b7d")
    timestamp = datetime(2020, 1, 2, tzinfo=timezone.utc)
    with postgres_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (username, hashed_password, created_at) VALUES (:name, :password, :created)"
        ), {"name": "preserved-user", "password": "unused", "created": timestamp})
    command.upgrade(alembic_config, "head")
    with postgres_engine.begin() as conn:
        assert conn.execute(text(
            "SELECT created_at FROM users WHERE username = 'preserved-user'"
        )).scalar_one() == timestamp
        conn.execute(text("INSERT INTO users (username, hashed_password) VALUES ('default-user', 'unused')"))
        assert conn.execute(text(
            "SELECT created_at FROM users WHERE username = 'default-user'"
        )).scalar_one() is not None
