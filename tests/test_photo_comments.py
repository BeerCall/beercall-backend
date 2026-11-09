from dataclasses import dataclass
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from core.security import create_access_token
from models.apero import Apero, AperoParticipant, AperoStatus, ParticipationStatus
from models.photo_comment import ProofPhotoComment
from models.realtime_event import RealtimeEvent
from models.squad import Squad
from models.user import User
from services.photo_comments import (
    create_photo_comment,
    delete_photo_comment,
    list_photo_comments,
)


@dataclass
class PhotoContext:
    owner: User
    author: User
    member: User
    outsider: User
    squad: Squad
    apero: Apero


@pytest.fixture
def photo_context(db_session: Session) -> PhotoContext:
    owner = User(username="owner", hashed_password="unused", push_token="owner-token")
    author = User(username="author", hashed_password="unused")
    member = User(username="member", hashed_password="unused")
    outsider = User(username="outsider", hashed_password="unused")
    squad = Squad(name="Squad", invite_code="PHOTO123", members=[owner, author, member])
    db_session.add(squad)
    db_session.add(outsider)
    db_session.flush()
    apero = Apero(
        squad_id=squad.id,
        creator_id=owner.id,
        status=AperoStatus.ACTIVE,
        latitude=48.8,
        longitude=2.3,
        location_name="Bar",
    )
    db_session.add(apero)
    db_session.flush()
    db_session.add(AperoParticipant(
        apero_id=apero.id,
        user_id=owner.id,
        status=ParticipationStatus.JOINED,
        photo_path="uploads/aperos/proof.jpg",
    ))
    db_session.commit()
    return PhotoContext(owner, author, member, outsider, squad, apero)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': user.username})}"}


def test_list_comments_checks_squad_membership_and_photo(
    db_session: Session,
    photo_context: PhotoContext,
) -> None:
    assert list_photo_comments(db_session, photo_context.squad.id, photo_context.apero.id, photo_context.owner.id, photo_context.member) == []

    with pytest.raises(PermissionError, match="Tu ne fais pas partie"):
        list_photo_comments(db_session, photo_context.squad.id, photo_context.apero.id, photo_context.owner.id, photo_context.outsider)

    with pytest.raises(LookupError, match="Preuve photo introuvable"):
        list_photo_comments(db_session, photo_context.squad.id, photo_context.apero.id, photo_context.author.id, photo_context.member)


def test_create_comment_notifies_owner_and_creates_realtime_event(
    db_session: Session,
    photo_context: PhotoContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = Mock()
    monkeypatch.setattr("services.photo_comments.send_push_notifications", sender)

    comment = create_photo_comment(
        db_session,
        photo_context.squad.id,
        photo_context.apero.id,
        photo_context.owner.id,
        photo_context.author,
        "  Petit joueur !  ",
    )

    assert comment.content == "Petit joueur !"
    assert comment.author_username == "author"
    assert comment.can_delete is True
    sender.assert_called_once()
    assert sender.call_args.kwargs["tokens"] == ["owner-token"]
    event = db_session.query(RealtimeEvent).one()
    assert event.payload["type"] == "PHOTO_COMMENT"
    assert event.payload["action"] == "CREATE"
    assert event.payload["comment_id"] == comment.id

    listed = list_photo_comments(
        db_session,
        photo_context.squad.id,
        photo_context.apero.id,
        photo_context.owner.id,
        photo_context.member,
    )
    assert [item.content for item in listed] == ["Petit joueur !"]
    assert listed[0].can_delete is False


def test_create_comment_on_own_photo_does_not_notify(
    db_session: Session,
    photo_context: PhotoContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = Mock()
    monkeypatch.setattr("services.photo_comments.send_push_notifications", sender)

    create_photo_comment(
        db_session,
        photo_context.squad.id,
        photo_context.apero.id,
        photo_context.owner.id,
        photo_context.owner,
        "C'est ma photo",
    )

    sender.assert_not_called()


@pytest.mark.parametrize("content", ["", "   ", "x" * 501])
def test_create_comment_rejects_invalid_content(
    db_session: Session,
    photo_context: PhotoContext,
    content: str,
) -> None:
    with pytest.raises(ValueError):
        create_photo_comment(
            db_session,
            photo_context.squad.id,
            photo_context.apero.id,
            photo_context.owner.id,
            photo_context.author,
            content,
        )
    assert db_session.query(ProofPhotoComment).count() == 0


def test_delete_comment_only_by_author(
    db_session: Session,
    photo_context: PhotoContext,
) -> None:
    comment = create_photo_comment(
        db_session,
        photo_context.squad.id,
        photo_context.apero.id,
        photo_context.owner.id,
        photo_context.author,
        "À supprimer",
    )

    with pytest.raises(PermissionError, match="tes propres commentaires"):
        delete_photo_comment(
            db_session,
            photo_context.squad.id,
            photo_context.apero.id,
            comment.id,
            photo_context.member,
        )

    delete_photo_comment(
        db_session,
        photo_context.squad.id,
        photo_context.apero.id,
        comment.id,
        photo_context.author,
    )
    assert db_session.query(ProofPhotoComment).count() == 0
    actions = [event.payload["action"] for event in db_session.query(RealtimeEvent).all()]
    assert "DELETE" in actions

    with pytest.raises(LookupError, match="Commentaire introuvable"):
        delete_photo_comment(
            db_session,
            photo_context.squad.id,
            photo_context.apero.id,
            comment.id,
            photo_context.author,
        )


def test_comments_api_contract(
    client: TestClient,
    photo_context: PhotoContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.photo_comments.send_push_notifications", Mock())
    base = f"/api/squads/{photo_context.squad.id}/beer-calls/bc_{photo_context.apero.id}/photos/u_{photo_context.owner.id}/comments"

    outsider = client.get(base, headers=_auth(photo_context.outsider))
    assert outsider.status_code == 403, outsider.text

    empty = client.post(base, json={"content": "   "}, headers=_auth(photo_context.author))
    assert empty.status_code == 422
    too_long = client.post(base, json={"content": "x" * 501}, headers=_auth(photo_context.author))
    assert too_long.status_code == 422

    created = client.post(base, json={"content": "Petit joueur !"}, headers=_auth(photo_context.author))
    assert created.status_code == 201
    body = created.json()
    assert body["author_username"] == "author"
    assert body["can_delete"] is True
    comment_id = body["id"]

    listed = client.get(base, headers=_auth(photo_context.member))
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["can_delete"] is False

    forbidden_delete = client.delete(
        f"/api/squads/{photo_context.squad.id}/beer-calls/bc_{photo_context.apero.id}/comments/{comment_id}",
        headers=_auth(photo_context.member),
    )
    assert forbidden_delete.status_code == 403

    deleted = client.delete(
        f"/api/squads/{photo_context.squad.id}/beer-calls/bc_{photo_context.apero.id}/comments/{comment_id}",
        headers=_auth(photo_context.author),
    )
    assert deleted.status_code == 204

    missing = client.delete(
        f"/api/squads/{photo_context.squad.id}/beer-calls/bc_{photo_context.apero.id}/comments/{comment_id}",
        headers=_auth(photo_context.author),
    )
    assert missing.status_code == 404
    assert client.get(base, headers=_auth(photo_context.member)).json() == []
