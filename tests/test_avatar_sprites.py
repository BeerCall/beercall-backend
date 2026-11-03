from pathlib import Path
from sqlalchemy.orm import Session

from models.avatar_render_job import AvatarRenderJob
from models.user import User
from schemas.avatar_sprites import AvatarSprites
from services import avatar_sprites
from services.avatar_sprites import (
    avatar_sprites_for,
    compute_config_hash,
    has_valid_published_sprites,
    normalize_avatar_payload,
    schedule_avatar_render,
)


def _valid_men_config() -> dict:
    return {
        "gender": "Men",
        "head": "Men_head_Casual_0",
        "body": "Men_body_Casual_0",
        "legs": "Men_legs_Casual_0",
        "feet": "Men_feet_Casual_0",
        "accessory": "none",
        "animation": "Idle",
    }


def test_hash_ignores_animation_and_treats_none_identically() -> None:
    base = _valid_men_config()

    norm1 = normalize_avatar_payload(base)
    assert norm1 is not None
    hash1 = compute_config_hash(norm1)

    # 1. Animation différente
    with_other_anim = dict(base, animation="Dancing")
    norm2 = normalize_avatar_payload(with_other_anim)
    assert norm2 is not None
    assert compute_config_hash(norm2) == hash1

    # 2. Accessory None vs "none" vs "" vs absent
    for acc in (None, "none", "None", ""):
        cfg = dict(base)
        cfg["accessory"] = acc
        norm = normalize_avatar_payload(cfg)
        assert norm is not None
        assert norm["accessory"] == "none"
        assert compute_config_hash(norm) == hash1

    del base["accessory"]
    norm_absent = normalize_avatar_payload(base)
    assert norm_absent is not None
    assert norm_absent["accessory"] == "none"
    assert compute_config_hash(norm_absent) == hash1

    # Format de hash : exactement 16 caractères hexadécimaux
    assert len(hash1) == 16
    assert avatar_sprites.HEX_16_RE.match(hash1)


def test_normalization_infers_gender_and_rejects_incomplete_or_invalid() -> None:
    # Déduction du genre sans champ explicite
    cfg_no_gender = {
        "head": "Women_head_Casual_0",
        "body": "Women_body_Casual_0",
        "legs": "Women_legs_Casual_0",
        "feet": "Women_feet_Casual_0",
    }
    norm = normalize_avatar_payload(cfg_no_gender)
    assert norm is not None
    assert norm["gender"] == "Women"

    # Pièce obligatoire manquante ou None
    for required in ("head", "body", "legs", "feet"):
        incomplete = dict(cfg_no_gender)
        incomplete[required] = None
        assert normalize_avatar_payload(incomplete) is None

    # Genre incohérent entre les pièces
    mixed = dict(cfg_no_gender, head="Men_head_Casual_0")
    assert normalize_avatar_payload(mixed) is None

    # Injection ou path traversal interdit
    malicious = dict(cfg_no_gender, head="../../evil_head")
    assert normalize_avatar_payload(malicious) is None


def test_schedule_when_disabled_sets_disabled_without_job(db_session: Session, monkeypatch) -> None:
    monkeypatch.setattr(avatar_sprites, "ENABLED", False)

    user = User(username="disabled_user", hashed_password="pw", avatar_config=_valid_men_config())
    db_session.add(user)
    db_session.flush()

    sprites = schedule_avatar_render(db_session, user)

    assert sprites.status == "disabled"
    assert sprites.pending is False
    assert user.avatar_render_status == "disabled"
    assert db_session.query(AvatarRenderJob).count() == 0


def test_schedule_empty_or_incomplete_config_sets_missing(db_session: Session) -> None:
    user = User(username="empty_user", hashed_password="pw", avatar_config={})
    db_session.add(user)
    db_session.flush()

    sprites = schedule_avatar_render(db_session, user)

    assert sprites.status == "missing"
    assert sprites.pending is False
    assert user.avatar_render_status == "missing"
    assert user.avatar_config_hash is None
    assert db_session.query(AvatarRenderJob).count() == 0


def test_schedule_cache_hit_when_sprites_already_published(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))

    cfg = _valid_men_config()
    norm = normalize_avatar_payload(cfg)
    assert norm is not None
    expected_hash = compute_config_hash(norm)

    # Création des trois sprites publiés dans le dossier uploads temporaire
    target_dir = tmp_path / "avatars" / "v1" / expected_hash
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "head.webp").write_bytes(b"RIFFdummyhead")
    (target_dir / "portrait.webp").write_bytes(b"RIFFdummyportrait")
    (target_dir / "body.webp").write_bytes(b"RIFFdummybody")

    assert has_valid_published_sprites(expected_hash) is True

    user = User(username="cached_user", hashed_password="pw", avatar_config=cfg)
    db_session.add(user)
    db_session.flush()

    sprites = schedule_avatar_render(db_session, user)

    assert sprites.status == "ready"
    assert sprites.pending is False
    assert sprites.hash == expected_hash
    assert sprites.head_url == f"/uploads/avatars/v1/{expected_hash}/head.webp"
    assert sprites.portrait_url == f"/uploads/avatars/v1/{expected_hash}/portrait.webp"
    assert sprites.body_url == f"/uploads/avatars/v1/{expected_hash}/body.webp"
    assert user.avatar_sprite_hash == expected_hash
    assert user.avatar_render_status == "ready"
    # Aucun job créé grâce au cache hit
    assert db_session.query(AvatarRenderJob).count() == 0


def test_schedule_cache_miss_creates_job_and_sets_pending(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))

    cfg = _valid_men_config()
    user = User(username="fresh_user", hashed_password="pw", avatar_config=cfg)
    db_session.add(user)
    db_session.flush()

    sprites = schedule_avatar_render(db_session, user)

    assert sprites.status == "pending"
    assert sprites.pending is True
    assert sprites.hash is None
    assert sprites.head_url is None
    assert user.avatar_render_status == "pending"

    job = db_session.query(AvatarRenderJob).first()
    assert job is not None
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.config_hash == user.avatar_config_hash


def test_schedule_re_queues_failed_job_with_zero_attempts(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))

    cfg = _valid_men_config()
    norm = normalize_avatar_payload(cfg)
    assert norm is not None
    h = compute_config_hash(norm)

    # Pré-création d'un job échoué
    failed_job = AvatarRenderJob(
        config_hash=h,
        avatar_config=norm,
        status="failed",
        attempts=3,
        last_error="Renderer timeout",
    )
    db_session.add(failed_job)
    db_session.flush()

    user = User(username="retry_user", hashed_password="pw", avatar_config=cfg)
    db_session.add(user)
    db_session.flush()

    sprites = schedule_avatar_render(db_session, user)

    assert sprites.status == "pending"
    db_session.refresh(failed_job)
    assert failed_job.status == "queued"
    assert failed_job.attempts == 0
    assert failed_job.last_error is None


def test_avatar_sprites_for_does_not_expose_outdated_published_hash() -> None:
    user = User(
        username="mismatch_user",
        hashed_password="pw",
        avatar_config_hash="0123456789abcdef",
        avatar_sprite_hash="fedcba9876543210",  # Ancien sprite ne correspondant plus à la tenue
        avatar_render_status="ready",
    )

    sprites = avatar_sprites_for(user)
    # L'ancienne version ne doit pas être affichée silencieusement
    assert sprites.status == "pending"
    assert sprites.pending is True
    assert sprites.hash is None
    assert sprites.portrait_url is None


def test_schedule_concurrent_sessions_save_both_users_with_single_job(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))

    import threading
    from db.database import engine
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine)
    results = []
    errors = []

    cfg = _valid_men_config()

    def worker(username: str):
        session = SessionLocal()
        try:
            user = User(username=username, hashed_password="pw", avatar_config=cfg)
            session.add(user)
            session.flush()

            sprites = schedule_avatar_render(session, user)
            session.commit()
            results.append((username, sprites, user.avatar_config_hash))
        except Exception as e:
            session.rollback()
            errors.append(e)
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=("u1_concurrent",))
    t2 = threading.Thread(target=worker, args=("u2_concurrent",))

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, f"Erreur dans un thread concurrent : {errors}"
    assert len(results) == 2
    assert results[0][1].status == "pending"
    assert results[1][1].status == "pending"

    # Vérification en base avec une nouvelle session
    check_session = SessionLocal()
    try:
        users = check_session.query(User).filter(User.username.in_(["u1_concurrent", "u2_concurrent"])).all()
        assert len(users) == 2
        assert all(u.avatar_render_status == "pending" for u in users)

        jobs = check_session.query(AvatarRenderJob).all()
        assert len(jobs) == 1
        assert jobs[0].config_hash == users[0].avatar_config_hash
    finally:
        check_session.close()
