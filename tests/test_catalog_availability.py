from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from core.security import create_access_token
from models.gamification import Skin
from models.user import User


def _available(db: Session, category: str, gender: str = "Men") -> Skin:
    skin = (
        db.query(Skin)
        .filter(
            Skin.available.is_(True),
            Skin.category == category,
            Skin.gender == gender,
        )
        .first()
    )
    assert skin is not None, f"Aucun article disponible pour {gender}/{category}"
    return skin


def _hidden_accessory(db: Session) -> Skin:
    skin = Skin(
        id="Men_accessory_HiddenCatalog_999",
        name="HiddenCatalog",
        category="accessory",
        gender="Men",
        price_caps=100,
        source=None,
        available=False,
    )
    db.add(skin)
    db.commit()
    return skin


def _user(db: Session, username: str, capsules: int = 1000) -> User:
    user = User(
        username=username,
        hashed_password="hashed",
        capsules=capsules,
        avatar_config={
            "head": _available(db, "head").id,
            "body": _available(db, "body").id,
            "legs": _available(db, "legs").id,
            "feet": _available(db, "feet").id,
            "accessory": None,
            "animation": "Idle",
            "gender": "Men",
        },
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': user.username})}"}


def test_guest_shop_excludes_hidden_and_exposes_readable_name(client: TestClient, db_session: Session):
    hidden = _hidden_accessory(db_session)

    response = client.get("/api/auth/profile/")

    assert response.status_code == 200
    items = response.json()["shop_items"]
    ids = {item["id"] for item in items}
    assert hidden.id not in ids
    beer = next(item for item in items if item["id"] == "Men_accessory_Beer_500")
    assert beer["name"] == "Accessoire Beer"


def test_own_shop_excludes_hidden(client: TestClient, db_session: Session):
    user = _user(db_session, "hidden_shop_user")
    hidden = _hidden_accessory(db_session)

    response = client.get("/api/auth/profile/", headers=_headers(user))

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["shop_items"]}
    assert hidden.id not in ids


def test_buy_hidden_item_is_refused_explicitly(client: TestClient, db_session: Session):
    user = _user(db_session, "hidden_buyer", capsules=1000)
    hidden = _hidden_accessory(db_session)

    response = client.post(
        "/api/auth/buy/",
        json={"item_id": hidden.id},
        headers=_headers(user),
    )

    assert response.status_code == 409
    assert "indisponible" in response.json()["detail"]
    db_session.refresh(user)
    assert user.capsules == 1000
    assert hidden not in user.skins


def test_equip_hidden_item_is_refused_explicitly(client: TestClient, db_session: Session):
    user = _user(db_session, "hidden_equipper")
    hidden = _hidden_accessory(db_session)
    avatar = dict(user.avatar_config)
    avatar["accessory"] = hidden.id

    response = client.put(
        "/api/auth/equip/",
        json=avatar,
        headers=_headers(user),
    )

    assert response.status_code == 409
    assert "indisponible" in response.json()["detail"]


def test_signup_with_hidden_item_is_refused_before_user_creation(client: TestClient, db_session: Session):
    hidden = _hidden_accessory(db_session)
    payload = {
        "username": "hidden_signup_user",
        "password": "Password123!",
        "avatar": {
            "head": _available(db_session, "head").id,
            "body": _available(db_session, "body").id,
            "legs": _available(db_session, "legs").id,
            "feet": _available(db_session, "feet").id,
            "accessory": hidden.id,
            "animation": "Idle",
            "gender": "Men",
        },
    }

    response = client.post("/api/auth/signup/", json=payload)

    assert response.status_code == 409
    assert "indisponible" in response.json()["detail"]
    assert db_session.query(User).filter_by(username="hidden_signup_user").count() == 0
