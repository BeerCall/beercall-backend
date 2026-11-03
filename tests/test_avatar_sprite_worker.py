import base64
import io
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from models.avatar_render_job import AvatarRenderJob
from models.user import User
from services import avatar_sprites
from workers import avatar_sprite_worker
from workers.avatar_sprite_worker import (
    claim_next_job,
    finalize_job_failure,
    finalize_job_success,
    process_one_job,
    publish_sprites_atomic,
    reap_expired_jobs,
    validate_sprite_image,
)


def _make_webp(width: int, height: int, color=(100, 150, 200, 255)) -> bytes:
    img = Image.new("RGBA", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


@pytest.fixture
def valid_sprites_bytes():
    return {
        "head": _make_webp(256, 256),
        "portrait": _make_webp(512, 512),
        "body": _make_webp(256, 512),
    }


def _mock_renderer_client(sprites_dict: dict, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code >= 400:
            return httpx.Response(status_code, text="Renderer error")
        payload = {
            "head": base64.b64encode(sprites_dict["head"]).decode("ascii"),
            "portrait": base64.b64encode(sprites_dict["portrait"]).decode("ascii"),
            "body": base64.b64encode(sprites_dict["body"]).decode("ascii"),
        }
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_validate_sprite_image_enforces_dimensions_format_and_alpha(valid_sprites_bytes):
    # Image valide
    validate_sprite_image(valid_sprites_bytes["head"], (256, 256), "head")

    # Dimensions incorrectes
    with pytest.raises(ValueError, match="Dimensions"):
        validate_sprite_image(valid_sprites_bytes["head"], (512, 512), "head")

    # Binaire non-WEBP (PNG)
    png_buf = io.BytesIO()
    Image.new("RGBA", (256, 256)).save(png_buf, format="PNG")
    with pytest.raises(ValueError, match="RIFF/WEBP"):
        validate_sprite_image(png_buf.getvalue(), (256, 256), "head")


def test_publish_sprites_atomic_creates_complete_directory_with_marker(
    tmp_path: Path, monkeypatch, valid_sprites_bytes
):
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))
    h = "0123456789abcdef"

    target = publish_sprites_atomic(
        h,
        valid_sprites_bytes["head"],
        valid_sprites_bytes["portrait"],
        valid_sprites_bytes["body"],
    )

    assert target.is_dir()
    assert (target / "head.webp").stat().st_size > 0
    assert (target / "portrait.webp").stat().st_size > 0
    assert (target / "body.webp").stat().st_size > 0
    assert (target / ".complete").is_file()
    assert avatar_sprites.sprites_published(h) is True


def test_process_one_job_success_publishes_and_updates_users(
    db_session: Session, tmp_path: Path, monkeypatch, valid_sprites_bytes
):
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))
    h = "1122334455667788"

    # Deux utilisateurs partageant la même tenue
    u1 = User(username="user_a", hashed_password="pw", avatar_config_hash=h, avatar_render_status="pending")
    u2 = User(username="user_b", hashed_password="pw", avatar_config_hash=h, avatar_render_status="pending")
    job = AvatarRenderJob(config_hash=h, avatar_config={"test": True}, status="queued")
    db_session.add_all([u1, u2, job])
    db_session.commit()

    mock_client = _mock_renderer_client(valid_sprites_bytes)

    from db.database import SessionLocal
    processed = process_one_job(SessionLocal, renderer_client=mock_client)
    assert processed is True

    # Vérification en base
    db_session.refresh(job)
    db_session.refresh(u1)
    db_session.refresh(u2)

    assert job.status == "done"
    assert job.owner_token is None
    assert u1.avatar_render_status == "ready"
    assert u1.avatar_sprite_hash == h
    assert u2.avatar_render_status == "ready"
    assert u2.avatar_sprite_hash == h
    assert avatar_sprites.sprites_published(h) is True


def test_process_one_job_already_published_finalizes_without_renderer(
    db_session: Session, tmp_path: Path, monkeypatch, valid_sprites_bytes
):
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))
    h = "aabbccddeeff0011"

    # Pré-publier les sprites
    publish_sprites_atomic(h, valid_sprites_bytes["head"], valid_sprites_bytes["portrait"], valid_sprites_bytes["body"])

    user = User(username="cached_w_user", hashed_password="pw", avatar_config_hash=h, avatar_render_status="pending")
    job = AvatarRenderJob(config_hash=h, avatar_config={"test": True}, status="queued")
    db_session.add_all([user, job])
    db_session.commit()

    # Client qui lèverait une exception si appelé
    def failing_handler(req):
        raise RuntimeError("Renderer ne doit pas être appelé !")

    mock_client = httpx.Client(transport=httpx.MockTransport(failing_handler))

    from db.database import SessionLocal
    processed = process_one_job(SessionLocal, renderer_client=mock_client)
    assert processed is True

    db_session.refresh(job)
    db_session.refresh(user)
    assert job.status == "done"
    assert user.avatar_render_status == "ready"


def test_process_one_job_retry_backoff_and_terminal_failure(
    db_session: Session, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))
    h = "ffeeddccbbaa9988"

    user = User(username="fail_user", hashed_password="pw", avatar_config_hash=h, avatar_render_status="pending")
    job = AvatarRenderJob(config_hash=h, avatar_config={"test": True}, status="queued", attempts=0)
    db_session.add_all([user, job])
    db_session.commit()

    failing_client = _mock_renderer_client({}, status_code=500)
    from db.database import SessionLocal

    # 1ère tentative échouée -> queued avec run_after dans ~30s
    process_one_job(SessionLocal, renderer_client=failing_client)
    db_session.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.run_after > datetime.now(timezone.utc)

    # Réinitialiser run_after pour la 2ème tentative
    job.run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    # 2ème tentative échouée -> queued avec run_after dans ~60s
    process_one_job(SessionLocal, renderer_client=failing_client)
    db_session.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 2

    # Réinitialiser run_after pour la 3ème tentative
    job.run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    # 3ème tentative échouée -> failed définitif et user marqué failed
    process_one_job(SessionLocal, renderer_client=failing_client)
    db_session.refresh(job)
    db_session.refresh(user)
    assert job.status == "failed"
    assert job.attempts == 3
    assert user.avatar_render_status == "failed"


def test_user_transition_A_to_B_during_render_A_does_not_corrupt_B(
    db_session: Session, tmp_path: Path, monkeypatch, valid_sprites_bytes
):
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))
    h_a = "aaaaaaaaaaaaaaaa"
    h_b = "bbbbbbbbbbbbbbbb"

    user = User(username="shifting_user", hashed_password="pw", avatar_config_hash=h_a, avatar_render_status="pending")
    job_a = AvatarRenderJob(config_hash=h_a, avatar_config={"test": True}, status="queued")
    db_session.add_all([user, job_a])
    db_session.commit()

    # Simulation : worker claim job A
    from db.database import SessionLocal
    claimed = claim_next_job(db_session, datetime.now(timezone.utc))
    assert claimed is not None
    job_id, config_hash, _, owner_token, _ = claimed

    # Pendant que le worker calcule A, l'utilisateur passe sur la tenue B
    user.avatar_config_hash = h_b
    user.avatar_render_status = "pending"
    db_session.commit()

    # Le worker A termine
    images = valid_sprites_bytes
    publish_sprites_atomic(h_a, images["head"], images["portrait"], images["body"])
    finalize_job_success(db_session, job_id, h_a, owner_token)

    # La tenue B de l'utilisateur n'a pas été écrasée par A
    db_session.refresh(user)
    assert user.avatar_config_hash == h_b
    assert user.avatar_render_status == "pending"
    assert user.avatar_sprite_hash != h_a


def test_reap_expired_jobs(db_session: Session):
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=15)

    # Job expiré tentative 1 -> remis en file
    j1 = AvatarRenderJob(config_hash="exp1111111111111", avatar_config={}, status="running", attempts=1, updated_at=old)
    # Job expiré tentative 3 -> failed définitif
    j3 = AvatarRenderJob(config_hash="exp3333333333333", avatar_config={}, status="running", attempts=3, updated_at=old)
    u3 = User(username="u_exp3", hashed_password="pw", avatar_config_hash="exp3333333333333", avatar_render_status="pending")
    db_session.add_all([j1, j3, u3])
    db_session.commit()

    reaped = reap_expired_jobs(db_session, now)
    assert reaped == 2

    db_session.refresh(j1)
    db_session.refresh(j3)
    db_session.refresh(u3)

    assert j1.status == "queued"
    assert j1.owner_token is None
    assert j3.status == "failed"
    assert u3.avatar_render_status == "failed"


def test_stale_owner_cas_prevents_finalization(db_session: Session):
    now = datetime.now(timezone.utc)
    job = AvatarRenderJob(config_hash="cas1111111111111", avatar_config={}, status="running", attempts=1, owner_token="new_owner")
    db_session.add(job)
    db_session.commit()

    # Un vieux worker avec l'ancien token tente de finaliser
    finalized = finalize_job_success(db_session, job.id, job.config_hash, "stale_owner_token")
    assert finalized is False

    db_session.refresh(job)
    assert job.status == "running"
    assert job.owner_token == "new_owner"


def test_two_concurrent_workers_claim_distinct_jobs(tmp_path: Path):
    from db.database import engine
    SessionMaker = sessionmaker(bind=engine)

    init_session = SessionMaker()
    now = datetime.now(timezone.utc)
    j1 = AvatarRenderJob(config_hash="c111111111111111", avatar_config={}, status="queued", run_after=now)
    j2 = AvatarRenderJob(config_hash="c222222222222222", avatar_config={}, status="queued", run_after=now)
    init_session.add_all([j1, j2])
    init_session.commit()
    init_session.close()

    claims = []

    def claim_worker():
        s = SessionMaker()
        try:
            res = claim_next_job(s, datetime.now(timezone.utc))
            if res:
                claims.append(res[1])  # config_hash
        finally:
            s.close()

    t1 = threading.Thread(target=claim_worker)
    t2 = threading.Thread(target=claim_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Chaque worker a obtenu un job distinct
    assert len(claims) == 2
    assert claims[0] != claims[1]
    assert set(claims) == {"c111111111111111", "c222222222222222"}
