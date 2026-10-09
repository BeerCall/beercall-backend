from unittest.mock import Mock

import pytest

from services import notifications
from core import realtime_relay
from models.realtime_event import RealtimeEvent
from models.squad import Squad
from models.user import User
from sqlalchemy.orm import sessionmaker


def test_transient_firebase_failure_is_retryable(monkeypatch):
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    monkeypatch.setattr(notifications.messaging, "send", Mock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        notifications.send_durable_push("token", "event")


def test_confirmed_recipients_are_not_resent(db_session, monkeypatch):
    creator = User(username="push-creator", hashed_password="unused", push_token="creator")
    first = User(username="push-first", hashed_password="unused", push_token="first")
    second = User(username="push-second", hashed_password="unused", push_token="second")
    squad = Squad(name="Push", invite_code="push-test", members=[creator, first, second])
    db_session.add(squad)
    db_session.flush()
    event = RealtimeEvent(squad_id=squad.id, payload={
        "type": "REFRESH_SQUAD", "action": "CREATE", "creator_id": creator.id,
    })
    db_session.add(event)
    db_session.commit()
    monkeypatch.setattr(realtime_relay, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    sender = Mock(side_effect=[None, RuntimeError("offline")])
    monkeypatch.setattr(realtime_relay, "send_durable_push", sender)
    with pytest.raises(RuntimeError, match="offline"):
        realtime_relay.dispatch_pushes(event.id)
    db_session.refresh(event)
    assert event.dispatched_at is None
    assert len(event.payload["push_completed"]) == 1
    sender.reset_mock(side_effect=True)
    realtime_relay.dispatch_pushes(event.id)
    assert sender.call_count == 1
    assert sender.call_args.args[0] != "creator"
