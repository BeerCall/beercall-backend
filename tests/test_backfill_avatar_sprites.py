from pathlib import Path
from sqlalchemy.orm import Session

from models.avatar_render_job import AvatarRenderJob
from models.user import User
from scripts.backfill_avatar_sprites import run_backfill
from services import avatar_sprites


def _valid_avatar(slot_suffix: str = "0") -> dict:
    return {
        "gender": "Men",
        "head": f"Men_head_Casual_{slot_suffix}",
        "body": "Men_body_Casual_0",
        "legs": "Men_legs_Casual_0",
        "feet": "Men_feet_Casual_0",
        "accessory": "none",
        "animation": "Idle",
    }


def test_dry_run_strict_does_not_modify_database(db_session: Session) -> None:
    u1 = User(username="dry1", hashed_password="pw", avatar_config=_valid_avatar("0"))
    u2 = User(username="dry2", hashed_password="pw", avatar_config={})
    db_session.add_all([u1, u2])
    db_session.commit()

    from db.database import SessionLocal
    summary = run_backfill(SessionLocal, batch_size=10, dry_run=True)

    assert summary["scanned"] == 2
    assert summary["created"] == 1
    assert summary["ignored"] == 1

    # Aucune modification en base
    assert db_session.query(AvatarRenderJob).count() == 0
    db_session.refresh(u1)
    db_session.refresh(u2)
    assert u1.avatar_render_status is None
    assert u1.avatar_config_hash is None


def test_backfill_idempotent_double_execution(db_session: Session) -> None:
    u1 = User(username="idem1", hashed_password="pw", avatar_config=_valid_avatar("0"))
    u2 = User(username="idem2", hashed_password="pw", avatar_config=_valid_avatar("1"))
    db_session.add_all([u1, u2])
    db_session.commit()

    from db.database import SessionLocal

    # 1ère passe : crée les 2 jobs
    s1 = run_backfill(SessionLocal, batch_size=10, dry_run=False)
    assert s1["scanned"] == 2
    assert s1["created"] == 2
    assert db_session.query(AvatarRenderJob).count() == 2

    # 2ème passe immédiate : 0 nouveau job créé (idempotent)
    s2 = run_backfill(SessionLocal, batch_size=10, dry_run=False)
    assert s2["scanned"] == 2
    assert s2["created"] == 0
    assert db_session.query(AvatarRenderJob).count() == 2


def test_batch_pagination_processes_all_records_incrementally(db_session: Session) -> None:
    users = [
        User(username=f"batch_user_{i}", hashed_password="pw", avatar_config=_valid_avatar(str(i)))
        for i in range(5)
    ]
    db_session.add_all(users)
    db_session.commit()

    from db.database import SessionLocal
    # batch_size=2 : nécessite 3 itérations de boucle
    summary = run_backfill(SessionLocal, batch_size=2, dry_run=False)

    assert summary["scanned"] == 5
    assert summary["created"] == 5
    assert db_session.query(AvatarRenderJob).count() == 5


def test_flag_disabled_marks_disabled_without_jobs(db_session: Session, monkeypatch) -> None:
    monkeypatch.setattr(avatar_sprites, "ENABLED", False)

    user = User(username="dis_user", hashed_password="pw", avatar_config=_valid_avatar("0"))
    db_session.add(user)
    db_session.commit()

    from db.database import SessionLocal
    summary = run_backfill(SessionLocal, batch_size=10, dry_run=False)

    assert summary["scanned"] == 1
    assert summary["ignored"] == 1
    assert summary["created"] == 0
    assert db_session.query(AvatarRenderJob).count() == 0

    db_session.refresh(user)
    assert user.avatar_render_status == "disabled"


def test_empty_config_marks_missing_without_jobs(db_session: Session) -> None:
    user = User(username="empty_cfg_user", hashed_password="pw", avatar_config=None)
    db_session.add(user)
    db_session.commit()

    from db.database import SessionLocal
    summary = run_backfill(SessionLocal, batch_size=10, dry_run=False)

    assert summary["scanned"] == 1
    assert summary["ignored"] == 1
    assert summary["created"] == 0
    assert db_session.query(AvatarRenderJob).count() == 0

    db_session.refresh(user)
    assert user.avatar_render_status == "missing"


def test_retry_failed_flag_re_queues_failed_jobs(db_session: Session) -> None:
    cfg = _valid_avatar("failed_item")
    from services.avatar_sprites import compute_config_hash, normalize_avatar_payload
    h = compute_config_hash(normalize_avatar_payload(cfg))

    user = User(
        username="failed_user",
        hashed_password="pw",
        avatar_config=cfg,
        avatar_config_hash=h,
        avatar_render_status="failed",
    )
    job = AvatarRenderJob(
        config_hash=h,
        avatar_config=cfg,
        status="failed",
        attempts=3,
        last_error="Ancien échec",
    )
    db_session.add_all([user, job])
    db_session.commit()

    from db.database import SessionLocal

    # Sans --retry-failed, le job reste failed et l'user est ignoré
    s_normal = run_backfill(SessionLocal, batch_size=10, dry_run=False, retry_failed=False)
    assert s_normal["scanned"] == 1
    assert s_normal["created"] == 0
    db_session.refresh(job)
    assert job.status == "failed"

    # Avec --retry-failed, le job est remis en queued
    s_retry = run_backfill(SessionLocal, batch_size=10, dry_run=False, retry_failed=True)
    assert s_retry["scanned"] == 1
    db_session.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.last_error is None
