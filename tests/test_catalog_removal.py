import pytest
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from models.gamification import Skin
from models.user import User
from services import avatar_catalog_sync
from services.avatar_catalog_sync import CatalogRemovalError, _reassign_users_for_removed_skin


def _skin(skin_id: str, category: str, gender: str, price: int, available: bool = True) -> Skin:
    return Skin(
        id=skin_id,
        name=skin_id.split("_")[2],
        category=category,
        gender=gender,
        price_caps=price,
        available=available,
        source=f"{skin_id}.fbx",
    )


def _user(username: str, config: dict) -> User:
    return User(username=username, hashed_password="pw", avatar_config=config)


def test_reassign_clothing_picks_first_free_source_id(db_session: Session, monkeypatch):
    removed = _skin("Men_head_Removed_0", "head", "Men", 100)
    free_b = _skin("Men_head_B_Free_0", "head", "Men", 0)
    free_a = _skin("Men_head_A_Free_0", "head", "Men", 0)
    user = _user("reassign_user", {
        "head": removed.id,
        "body": "Men_body_Casual_0",
        "legs": "Men_legs_Casual_0",
        "feet": "Men_feet_Casual_0",
        "accessory": "none",
    })
    db_session.add_all([removed, free_b, free_a, user])
    db_session.commit()

    scheduled = []
    monkeypatch.setattr(
        avatar_catalog_sync,
        "schedule_avatar_render",
        lambda db, target: scheduled.append(target.id),
    )

    count = _reassign_users_for_removed_skin(db_session, removed)
    db_session.commit()

    assert count == 1
    assert user.avatar_config["head"] == "Men_head_A_Free_0"
    assert any(skin.id == "Men_head_A_Free_0" for skin in user.skins)
    assert scheduled == [user.id]


def test_reassign_accessory_sets_none(db_session: Session, monkeypatch):
    removed = _skin("Men_accessory_Removed_0", "accessory", "Men", 500)
    user = _user("accessory_user", {
        "head": "Men_head_Casual_0",
        "body": "Men_body_Casual_0",
        "legs": "Men_legs_Casual_0",
        "feet": "Men_feet_Casual_0",
        "accessory": removed.id,
    })
    db_session.add_all([removed, user])
    db_session.commit()

    scheduled = []
    monkeypatch.setattr(
        avatar_catalog_sync,
        "schedule_avatar_render",
        lambda db, target: scheduled.append(target.id),
    )

    count = _reassign_users_for_removed_skin(db_session, removed)
    db_session.commit()

    assert count == 1
    assert user.avatar_config["accessory"] == "none"
    assert scheduled == [user.id]


def test_reassign_without_free_piece_fails_explicitly(db_session: Session, monkeypatch):
    removed = _skin("Kids_head_Removed_0", "head", "Kids", 100)
    user = _user("kids_user", {
        "head": removed.id,
        "body": "Men_body_Casual_0",
        "legs": "Men_legs_Casual_0",
        "feet": "Men_feet_Casual_0",
        "accessory": "none",
    })
    db_session.add_all([removed, user])
    db_session.commit()

    monkeypatch.setattr(avatar_catalog_sync, "schedule_avatar_render", lambda db, target: None)

    with pytest.raises(CatalogRemovalError) as error:
        _reassign_users_for_removed_skin(db_session, removed)

    assert removed.id in str(error.value)
    assert "Aucune pièce gratuite" in str(error.value)
