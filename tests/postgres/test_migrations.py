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
        assert "alembic_version" in tables
        
    command.upgrade(alembic_config, "head")
