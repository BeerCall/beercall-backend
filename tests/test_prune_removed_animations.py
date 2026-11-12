import json

import pytest
from sqlalchemy.orm import sessionmaker

import scripts.prune_removed_animations as prune_script
from models.user import User
from services.animation_pruning import (
    apply_removed_animations,
    find_removed_animations,
    valid_animation_names,
)


def add_user(db, username: str, animation: str) -> User:
    user = User(
        username=username,
        hashed_password="x",
        avatar_config={
            "head": "Men_head_Casual_0",
            "body": "Men_body_Casual_0",
            "legs": "Men_legs_Casual_0",
            "feet": "Men_feet_Casual_0",
            "accessory": "none",
            "gender": "Men",
            "animation": animation,
        },
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_valid_animation_names_requires_idle() -> None:
    with pytest.raises(ValueError, match="Idle"):
        valid_animation_names({"clips": [{"name": "Walk"}]})
    assert valid_animation_names({"clips": [{"name": "Idle"}, {"name": "Walk"}]}) == {"Idle", "Walk"}


def test_find_removed_animations(db_session) -> None:
    idle = add_user(db_session, "idle-user", "Idle")
    walk = add_user(db_session, "walk-user", "Walk")
    removed = add_user(db_session, "removed-user", "Removed_Clip")
    no_animation = User(username="no-animation", hashed_password="x", avatar_config={"head": "x"})
    db_session.add(no_animation)
    db_session.commit()

    removals = find_removed_animations(
        [idle, walk, removed, no_animation],
        {"Idle", "Walk"},
    )

    assert [(item.user_id, item.animation) for item in removals] == [
        (removed.id, "Removed_Clip"),
    ]


def test_apply_removed_animations_resets_only_removed(db_session) -> None:
    idle = add_user(db_session, "idle-user", "Idle")
    removed = add_user(db_session, "removed-user", "Removed_Clip")

    removals = find_removed_animations([idle, removed], {"Idle", "Walk"})
    assert apply_removed_animations(db_session, removals) == 1
    db_session.commit()
    db_session.refresh(idle)
    db_session.refresh(removed)

    assert idle.avatar_config["animation"] == "Idle"
    assert removed.avatar_config["animation"] == "Idle"
    assert removed.avatar_config["head"] == "Men_head_Casual_0"


def test_script_simulation_does_not_write(db_session, monkeypatch, capsys) -> None:
    removed = add_user(db_session, "removed-user", "Removed_Clip")
    keep = add_user(db_session, "keep-user", "Walk")
    TestSession = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(prune_script, "SessionLocal", TestSession)

    assert prune_script.main(["--valid-animations", "Idle,Walk", "--json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "SIMULATION"
    assert output["usersToReset"] == 1
    assert output["usersReset"] == 0
    assert output["removals"] == [
        {"userId": removed.id, "animation": "Removed_Clip", "replacement": "Idle"},
    ]

    db_session.refresh(removed)
    db_session.refresh(keep)
    assert removed.avatar_config["animation"] == "Removed_Clip"
    assert keep.avatar_config["animation"] == "Walk"
