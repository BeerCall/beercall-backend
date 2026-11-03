from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from main import app
from models.apero import Apero, AperoParticipant, ParticipationStatus
from models.squad import Squad
from models.user import User
from services import avatar_sprites

client = TestClient(app)


def _auth_headers(username: str) -> dict:
    from core.security import create_access_token
    token = create_access_token({"sub": username})
    return {"Authorization": f"Bearer {token}"}


def _avatar(gender: str = "Men") -> dict:
    return {
        "gender": gender,
        "head": f"{gender}_head_Casual_0",
        "body": f"{gender}_body_Casual_0",
        "legs": f"{gender}_legs_Casual_0",
        "feet": f"{gender}_feet_Casual_0",
        "accessory": "none",
        "animation": "Idle",
    }


def test_worlds_participants_include_avatar_sprites_in_bar_piscine_and_dodo(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(avatar_sprites, "UPLOADS_DIR", str(tmp_path))

    # Sprites prêts pour l'utilisateur du bar
    h_ready = "1234567890abcdef"
    sdir = tmp_path / "avatars" / "v1" / h_ready
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "head.webp").write_bytes(b"head")
    (sdir / "portrait.webp").write_bytes(b"portrait")
    (sdir / "body.webp").write_bytes(b"body")

    u_bar = User(
        username="u_bar",
        hashed_password="pw",
        avatar_config=_avatar("Men"),
        avatar_render_status="ready",
        avatar_config_hash=h_ready,
        avatar_sprite_hash=h_ready,
    )
    u_piscine = User(
        username="u_piscine",
        hashed_password="pw",
        avatar_config=_avatar("Women"),
        avatar_render_status="pending",
        avatar_config_hash="fedcba0987654321",
    )
    u_dodo = User(
        username="u_dodo",
        hashed_password="pw",
        avatar_config=_avatar("Men"),
        avatar_render_status="missing",
    )

    squad = Squad(name="Squad 3D Worlds", invite_code="SQWORLDS1")
    squad.members.extend([u_bar, u_piscine, u_dodo])
    db_session.add_all([u_bar, u_piscine, u_dodo, squad])
    db_session.commit()

    apero = Apero(
        squad_id=squad.id,
        creator_id=u_bar.id,
        latitude=48.8566,
        longitude=2.3522,
        location_name="Bar du coin",
    )
    db_session.add(apero)
    db_session.commit()

    p_joined = AperoParticipant(
        apero_id=apero.id,
        user_id=u_bar.id,
        status=ParticipationStatus.JOINED,
        photo_path="uploads/proof.jpg",
    )
    p_declined = AperoParticipant(
        apero_id=apero.id,
        user_id=u_piscine.id,
        status=ParticipationStatus.DECLINED,
        excuse="Piscine aujourd'hui !",
    )
    db_session.add_all([p_joined, p_declined])
    db_session.commit()

    # Requête des mondes par u_bar
    headers = _auth_headers("u_bar")
    res = client.get(f"/api/squads/{squad.id}/beer-calls/bc_{apero.id}/worlds", headers=headers)
    assert res.status_code == 200
    data = res.json()["worlds"]

    # 1. Bar : u_bar est présent, statut ready, URLs valides
    bar_part = data["bar"]["participants"]
    assert len(bar_part) == 1
    assert bar_part[0]["username"] == "u_bar"
    assert bar_part[0]["avatar_sprites"]["status"] == "ready"
    assert bar_part[0]["avatar_sprites"]["hash"] == h_ready
    assert bar_part[0]["avatar_sprites"]["portrait_url"] == f"/uploads/avatars/v1/{h_ready}/portrait.webp"

    # 2. Piscine : u_piscine est présente, statut pending
    piscine_part = data["piscine"]["participants"]
    assert len(piscine_part) == 1
    assert piscine_part[0]["username"] == "u_piscine"
    assert piscine_part[0]["avatar_sprites"]["status"] == "pending"
    assert piscine_part[0]["avatar_sprites"]["portrait_url"] is None

    # 3. Dodo : u_dodo n'a pas répondu, statut missing
    dodo_part = data["dodo"]["participants"]
    assert len(dodo_part) == 1
    assert dodo_part[0]["username"] == "u_dodo"
    assert dodo_part[0]["avatar_sprites"]["status"] == "missing"


def test_worlds_authorization_and_squad_isolation(db_session: Session) -> None:
    u1 = User(username="member_squad1", hashed_password="pw", avatar_config=_avatar("Men"))
    u_outsider = User(username="outsider", hashed_password="pw", avatar_config=_avatar("Women"))
    squad1 = Squad(name="Squad 1", invite_code="SQ1111")
    squad2 = Squad(name="Squad 2", invite_code="SQ2222")

    squad1.members.append(u1)
    squad2.members.append(u_outsider)
    db_session.add_all([u1, u_outsider, squad1, squad2])
    db_session.commit()

    apero_s1 = Apero(squad_id=squad1.id, creator_id=u1.id, latitude=48.8, longitude=2.3)
    apero_s2 = Apero(squad_id=squad2.id, creator_id=u_outsider.id, latitude=48.8, longitude=2.3)
    db_session.add_all([apero_s1, apero_s2])
    db_session.commit()

    # 1. Non-membre de squad1 tente d'accéder aux mondes de squad1 -> 403
    res_403 = client.get(
        f"/api/squads/{squad1.id}/beer-calls/bc_{apero_s1.id}/worlds",
        headers=_auth_headers("outsider"),
    )
    assert res_403.status_code == 403

    # 2. Membre de squad1 tente d'accéder avec l'ID d'un apero de squad2 (tentative de fuite inter-squad) -> 404
    res_404 = client.get(
        f"/api/squads/{squad1.id}/beer-calls/bc_{apero_s2.id}/worlds",
        headers=_auth_headers("member_squad1"),
    )
    assert res_404.status_code == 404
    assert "Apéro introuvable dans cette squad" in res_404.json()["detail"]
