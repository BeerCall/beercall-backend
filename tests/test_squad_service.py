import re
import pytest

from sqlalchemy.orm import Session

from models.squad import Squad
from models.user import User
from schemas.squad import SquadCreate
from services.squads import create_squad, join_squad


def make_user(db: Session, username: str = "creator") -> User:
    user = User(username=username, hashed_password="unused")
    db.add(user)
    db.commit()
    return user


def test_create_preserves_fields_and_commits(db_session: Session) -> None:
    user = make_user(db_session)
    data = SquadCreate(name="Squad", icon="🍻", color="#123456")
    squad = create_squad(db_session, user, data)
    squad_id = squad.id
    db_session.rollback()
    db_session.expire_all()
    stored = db_session.get(Squad, squad_id)
    assert stored is not None
    assert (stored.name, stored.icon, stored.color) == (data.name, data.icon, data.color)


def test_create_adds_creator_as_only_member(db_session: Session) -> None:
    user = make_user(db_session)
    squad = create_squad(db_session, user, SquadCreate(name="Squad", icon="🍻", color="#123456"))
    db_session.expire_all()
    assert [member.id for member in squad.members] == [user.id]
    assert squad in user.squads


def test_create_generates_distinct_uppercase_codes(db_session: Session) -> None:
    user = make_user(db_session)
    data = SquadCreate(name="Squad", icon="🍻", color="#123456")
    first = create_squad(db_session, user, data)
    second = create_squad(db_session, user, data)
    assert re.fullmatch(r"[0-9A-F]{8}", first.invite_code)
    assert re.fullmatch(r"[0-9A-F]{8}", second.invite_code)
    assert first.invite_code != second.invite_code


def test_join_commits_membership(db_session: Session) -> None:
    creator = make_user(db_session)
    user = make_user(db_session, "joiner")
    squad = create_squad(db_session, creator, SquadCreate(name="Squad", icon="🍻", color="#123456"))
    joined = join_squad(db_session, user, squad.invite_code)
    assert joined.id == squad.id
    db_session.rollback()
    db_session.expire_all()
    assert {member.id for member in squad.members} == {creator.id, user.id}


def test_join_normalizes_lowercase_code(db_session: Session) -> None:
    creator = make_user(db_session)
    user = make_user(db_session, "joiner")
    squad = create_squad(db_session, creator, SquadCreate(name="Squad", icon="🍻", color="#123456"))
    squad.invite_code = "ABCDEF12"
    db_session.commit()
    assert join_squad(db_session, user, "abcdef12").id == squad.id


def test_join_rejects_unknown_code(db_session: Session) -> None:
    user = make_user(db_session)
    with pytest.raises(LookupError, match=r"^Code d'invitation invalide\.$"):
        join_squad(db_session, user, "UNKNOWN1")
    assert user.squads == []


def test_join_rejects_existing_member(db_session: Session) -> None:
    user = make_user(db_session)
    squad = create_squad(db_session, user, SquadCreate(name="Squad", icon="🍻", color="#123456"))
    with pytest.raises(ValueError, match="^Tu fais déjà partie de cette Squad !$"):
        join_squad(db_session, user, squad.invite_code)
    assert squad.members == [user]
