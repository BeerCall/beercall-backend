from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models.gamification import Skin
from services.avatar_catalog import display_name_for


def _make_skin(db: Session, skin_id: str, category: str, name: str, available: bool, price: int = 0):
    skin = Skin(
        id=skin_id,
        name=name,
        gender="Men",
        category=category,
        price_caps=price,
        available=available,
        source=f"{skin_id}.fbx",
    )
    db.add(skin)
    db.flush()
    return skin


def _seed_pieces(db: Session, prefix: str, available: bool = True):
    ids = {}
    for category in ("head", "body", "legs", "feet"):
        skin_id = f"Men_{category}_{prefix}_0"
        _make_skin(db, skin_id, category, prefix, available)
        ids[category] = skin_id
    db.commit()
    return ids


def _signup(client: TestClient, username: str, pieces: dict, accessory: str | None = None):
    payload = {
        "username": username,
        "password": "Password123!",
        "avatar": {
            "head": pieces["head"],
            "body": pieces["body"],
            "legs": pieces["legs"],
            "feet": pieces["feet"],
            "accessory": accessory,
            "animation": "idle",
            "gender": "Men",
        },
    }
    response = client.post("/api/auth/signup/", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_signup_unavailable_detail_uses_catalog_name(client: TestClient, db_session: Session):
    pieces = _seed_pieces(db_session, "SignupLabel")
    accessory = _make_skin(
        db_session,
        "Men_accessory_EtiquetteTest_500",
        "accessory",
        "EtiquetteTest",
        available=False,
        price=500,
    )
    db_session.commit()

    response = client.post(
        "/api/auth/signup/",
        json={
            "username": "signup_label_user",
            "password": "Password123!",
            "avatar": {
                "head": pieces["head"],
                "body": pieces["body"],
                "legs": pieces["legs"],
                "feet": pieces["feet"],
                "accessory": accessory.id,
                "animation": "idle",
                "gender": "Men",
            },
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert display_name_for("EtiquetteTest", "accessory") in detail
    assert accessory.id not in detail


def test_buy_unavailable_detail_uses_catalog_name(client: TestClient, db_session: Session):
    pieces = _seed_pieces(db_session, "BuyLabel")
    accessory = _make_skin(
        db_session,
        "Men_accessory_EtiquetteTest_500",
        "accessory",
        "EtiquetteTest",
        available=False,
        price=500,
    )
    db_session.commit()
    token = _signup(client, "buy_label_user", pieces)

    response = client.post(
        "/api/auth/buy/",
        json={"item_id": accessory.id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert display_name_for("EtiquetteTest", "accessory") in detail
    assert accessory.id not in detail


def test_equip_unavailable_detail_uses_catalog_name(client: TestClient, db_session: Session):
    pieces = _seed_pieces(db_session, "EquipLabel")
    accessory = _make_skin(
        db_session,
        "Men_accessory_EtiquetteTest_500",
        "accessory",
        "EtiquetteTest",
        available=False,
        price=500,
    )
    db_session.commit()
    token = _signup(client, "equip_label_user", pieces)

    response = client.put(
        "/api/auth/equip/",
        json={
            "head": pieces["head"],
            "body": pieces["body"],
            "legs": pieces["legs"],
            "feet": pieces["feet"],
            "accessory": accessory.id,
            "animation": "idle",
            "gender": "Men",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert display_name_for("EtiquetteTest", "accessory") in detail
    assert accessory.id not in detail


def test_equip_unowned_detail_uses_catalog_name(client: TestClient, db_session: Session):
    pieces = _seed_pieces(db_session, "UnownedLabel")
    accessory = _make_skin(
        db_session,
        "Men_accessory_EtiquetteTest_500",
        "accessory",
        "EtiquetteTest",
        available=True,
        price=500,
    )
    db_session.commit()
    token = _signup(client, "unowned_label_user", pieces)

    response = client.put(
        "/api/auth/equip/",
        json={
            "head": pieces["head"],
            "body": pieces["body"],
            "legs": pieces["legs"],
            "feet": pieces["feet"],
            "accessory": accessory.id,
            "animation": "idle",
            "gender": "Men",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert display_name_for("EtiquetteTest", "accessory") in detail
    assert accessory.id not in detail
