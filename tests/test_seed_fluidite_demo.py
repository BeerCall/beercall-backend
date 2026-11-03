import os
from unittest.mock import MagicMock
import pytest

from scripts.seed_fluidite_demo import (
    assert_safe_environment,
    generate_distinct_configs,
    seed_fluidite_demo,
)
from services.avatar_sprites import compute_config_hash, normalize_avatar_payload
from models.user import User
from models.squad import Squad
from models.apero import Apero, AperoParticipant, ParticipationStatus
from models.avatar_render_job import AvatarRenderJob


def test_assert_safe_environment_refuses_production():
    mock_db = MagicMock()
    mock_db.get_bind().url.database = "beercall_fluidite_demo"
    mock_db.get_bind().url.host = "localhost"

    # Test avec APP_ENV absent ou production
    old_env = os.environ.get("APP_ENV")
    try:
        os.environ["APP_ENV"] = "production"
        with pytest.raises(RuntimeError, match="APP_ENV doit valoir 'test'"):
            assert_safe_environment(mock_db)

        os.environ["APP_ENV"] = "test"
        # Test avec mauvaise base
        mock_db.get_bind().url.database = "beercall_prod"
        with pytest.raises(RuntimeError, match="base interdite"):
            assert_safe_environment(mock_db)

        # Test avec mauvais hôte
        mock_db.get_bind().url.database = "beercall_fluidite_demo"
        mock_db.get_bind().url.host = "remote-prod-host.internal"
        with pytest.raises(RuntimeError, match="hôte base non autorisé"):
            assert_safe_environment(mock_db)

        # Environnement valide
        mock_db.get_bind().url.host = "localhost"
        assert_safe_environment(mock_db)  # Ne lève pas
    finally:
        if old_env is not None:
            os.environ["APP_ENV"] = old_env
        else:
            os.environ.pop("APP_ENV", None)


def test_generate_distinct_configs_produces_50_unique_hashes():
    configs = generate_distinct_configs(50)
    assert len(configs) == 50

    hashes = []
    for cfg in configs:
        norm = normalize_avatar_payload(cfg)
        assert norm is not None
        h = compute_config_hash(norm)
        hashes.append(h)

    assert len(set(hashes)) == 50, "Chaque configuration doit générer un hash visuel distinct"
    assert configs[0]["accessory"] == "Men_accessory_Pikachu_2000"
    assert "accessory_transform" in configs[0]


def test_seed_fluidite_demo_creates_squads_users_aperos_and_jobs(db_session, monkeypatch, tmp_path):
    # Simuler les gardes de DB pour le test local A00
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr("scripts.seed_fluidite_demo.assert_safe_environment", lambda _db: None)

    result = seed_fluidite_demo(db_session, uploads_dir=tmp_path)

    assert result["status"] == "success"
    assert result["users_count"] == 50

    # Vérifications en base
    demo_user = db_session.query(User).filter_by(username="FluiditeDemo").first()
    assert demo_user is not None
    assert demo_user.capsules >= 5000
    assert len(demo_user.skins) >= 4

    # Squads
    squad50 = db_session.query(Squad).filter_by(invite_code="FLUID50").first()
    assert squad50 is not None
    assert len(squad50.members) == 50

    squad40 = db_session.query(Squad).filter_by(invite_code="FLUID40").first()
    assert squad40 is not None
    assert len(squad40.members) == 40

    # Apéro 40
    apero40 = db_session.query(Apero).filter_by(id=result["apero40_id"]).first()
    assert apero40 is not None
    participants = db_session.query(AperoParticipant).filter_by(apero_id=apero40.id).all()
    assert len(participants) == 40

    joined = [p for p in participants if p.status == ParticipationStatus.JOINED]
    assert len(joined) == 10
    declined = [p for p in participants if p.status == ParticipationStatus.DECLINED]
    assert len(declined) == 10
    ghost = [p for p in participants if p.status == ParticipationStatus.GHOST]
    assert len(ghost) == 20

    # Photos de preuve réelles Pillow (une par participant Bar)
    joined_with_photo = [p for p in joined if p.photo_path is not None]
    assert len(joined_with_photo) == 10
    assert all(p.photo_path.startswith("/uploads/proofs/") for p in joined_with_photo)

    # Jobs de sprites planifiés (50 jobs queued)
    jobs = db_session.query(AvatarRenderJob).all()
    assert len(jobs) >= 50
    assert all(job.status in ("queued", "done") for job in jobs)
