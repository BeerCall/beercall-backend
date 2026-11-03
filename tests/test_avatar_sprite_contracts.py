from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from main import app
from models.avatar_render_job import AvatarRenderJob
from models.gamification import Skin
from models.squad import Squad
from models.user import User
from services import avatar_sprites

client = TestClient(app)


def _auth_headers(username: str) -> dict:
    from core.security import create_access_token
    token = create_access_token({"sub": username})
    return {"Authorization": f"Bearer {token}"}


def _valid_avatar() -> dict:
    return {
        "gender": "Men",
        "head": "Men_head_Casual_0",
        "body": "Men_body_Casual_0",
        "legs": "Men_legs_Casual_0",
        "feet": "Men_feet_Casual_0",
        "accessory": "none",
        "animation": "Idle",
    }


def test_signup_creates_render_job_and_sets_pending(db_session: Session) -> None:
    payload = {
        "username": "signup_sprites_user",
        "password": "Password123!",
        "avatar": _valid_avatar(),
    }

    response = client.post("/api/auth/signup/", json=payload)
    assert response.status_code == 200

    user = db_session.query(User).filter_by(username="signup_sprites_user").one()
    assert user.avatar_render_status == "pending"
    assert user.avatar_config_hash is not None

    job = db_session.query(AvatarRenderJob).filter_by(config_hash=user.avatar_config_hash).one()
    assert job.status == "queued"
    assert job.attempts == 0


def test_equip_anti_cheat_rejection_does_not_create_job(db_session: Session) -> None:
    user = User(username="cheater_user", hashed_password="pw", avatar_config=_valid_avatar())
    db_session.add(user)
    db_session.commit()

    headers = _auth_headers("cheater_user")
    equip_payload = {
        "gender": "Men",
        "head": "Men_head_Casual_0",
        "body": "Men_body_Casual_0",
        "legs": "Men_legs_Casual_0",
        "feet": "Men_feet_Casual_0",
        "accessory": "Men_accessory_Unowned_999",  # Non possédé
        "animation": "Idle",
    }

    response = client.put("/api/auth/equip/", json=equip_payload, headers=headers)
    assert response.status_code == 403
    assert "Triche détectée" in response.json()["detail"]

    # Aucun job créé
    assert db_session.query(AvatarRenderJob).count() == 0


def test_equip_valid_updates_config_and_enqueues_job(db_session: Session) -> None:
    # Créer un utilisateur possédant les skins
    user = User(username="valid_equipper", hashed_password="pw", avatar_config=_valid_avatar())
    skins = [
        Skin(id="Men_head_Casual_0", name="Head", category="head", gender="Men", price_caps=0),
        Skin(id="Men_body_Casual2_0", name="Body2", category="body", gender="Men", price_caps=0),
        Skin(id="Men_legs_Casual_0", name="Legs", category="legs", gender="Men", price_caps=0),
        Skin(id="Men_feet_Casual_0", name="Feet", category="feet", gender="Men", price_caps=0),
    ]
    user.skins.extend(skins)
    db_session.add(user)
    db_session.commit()

    headers = _auth_headers("valid_equipper")
    new_avatar = {
        "gender": "Men",
        "head": "Men_head_Casual_0",
        "body": "Men_body_Casual2_0",
        "legs": "Men_legs_Casual_0",
        "feet": "Men_feet_Casual_0",
        "accessory": "none",
        "animation": "Idle",
    }

    response = client.put("/api/auth/equip/", json=new_avatar, headers=headers)
    assert response.status_code == 200
    # Contrat existant préservé : message et avatar
    data = response.json()
    assert "Ta nouvelle tenue est magnifique" in data["message"]
    assert data["avatar"]["body"] == "Men_body_Casual2_0"

    db_session.refresh(user)
    assert user.avatar_render_status == "pending"
    job = db_session.query(AvatarRenderJob).filter_by(config_hash=user.avatar_config_hash).one()
    assert job.status == "queued"


def test_profile_endpoints_contract_shapes_and_states(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))

    # 1. Profil Guest (anonyme)
    guest_res = client.get("/api/auth/profile/")
    assert guest_res.status_code == 200
    guest_data = guest_res.json()
    assert guest_data["avatar_sprites"] == {
        "hash": None,
        "head_url": None,
        "portrait_url": None,
        "body_url": None,
        "pending": False,
        "status": "missing",
    }

    # 2. Utilisateur connecté avec rendu pending
    user1 = User(
        username="user1_contract",
        hashed_password="pw",
        avatar_config=_valid_avatar(),
        avatar_render_status="pending",
        avatar_config_hash="0123456789abcdef",
    )
    # Utilisateur avec rendu ready (sprites publiés)
    h_ready = "abcdef0123456789"
    sprites_dir = tmp_path / "avatars" / "v1" / h_ready
    sprites_dir.mkdir(parents=True, exist_ok=True)
    (sprites_dir / "head.webp").write_bytes(b"head")
    (sprites_dir / "portrait.webp").write_bytes(b"portrait")
    (sprites_dir / "body.webp").write_bytes(b"body")

    user2 = User(
        username="user2_contract",
        hashed_password="pw",
        avatar_config=_valid_avatar(),
        avatar_render_status="ready",
        avatar_config_hash=h_ready,
        avatar_sprite_hash=h_ready,
    )

    squad = Squad(name="Squad Sprites", invite_code="SQSPRITES1")
    squad.members.extend([user1, user2])
    db_session.add_all([user1, user2, squad])
    db_session.commit()

    # /api/auth/me (pending)
    me_res = client.get("/api/auth/me", headers=_auth_headers("user1_contract"))
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["avatar_sprites"]["status"] == "pending"
    assert me_data["avatar_sprites"]["pending"] is True
    assert me_data["avatar_sprites"]["portrait_url"] is None

    # /api/auth/profile/ (soi-même ready)
    self_res = client.get("/api/auth/profile/", headers=_auth_headers("user2_contract"))
    assert self_res.status_code == 200
    self_data = self_res.json()
    assert self_data["avatar_sprites"]["status"] == "ready"
    assert self_data["avatar_sprites"]["pending"] is False
    assert self_data["avatar_sprites"]["hash"] == h_ready
    assert self_data["avatar_sprites"]["portrait_url"] == f"/uploads/avatars/v1/{h_ready}/portrait.webp"

    # /api/auth/profile/{id}/ (autrui)
    other_res = client.get(f"/api/auth/profile/{user2.id}/", headers=_auth_headers("user1_contract"))
    assert other_res.status_code == 200
    other_data = other_res.json()
    assert other_data["avatar_sprites"]["status"] == "ready"
    assert other_data["avatar_sprites"]["hash"] == h_ready

    # /api/auth/connections/ (les deux branches connexions)
    conn_res = client.get("/api/auth/connections/", headers=_auth_headers("user1_contract"))
    assert conn_res.status_code == 200
    conn_data = conn_res.json()
    by_name = {item["username"]: item for item in conn_data}
    assert by_name["user1_contract"]["avatar_sprites"]["status"] == "pending"
    assert by_name["user2_contract"]["avatar_sprites"]["status"] == "ready"
    assert by_name["user2_contract"]["avatar_sprites"]["portrait_url"] == f"/uploads/avatars/v1/{h_ready}/portrait.webp"
