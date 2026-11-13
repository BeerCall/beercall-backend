import asyncio
from unittest.mock import Mock

import pytest
from firebase_admin import exceptions, messaging
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from core import realtime_relay
from models.realtime_event import RealtimeEvent
from models.squad import Squad
from models.user import User
from services import notifications


def _squad_with_members(db_session, tokens: dict[str, str | None]) -> tuple[Squad, dict[str, User]]:
    users = {
        name: User(username=name, hashed_password="unused", push_token=token)
        for name, token in tokens.items()
    }
    squad = Squad(name="Push", invite_code="push-test", members=list(users.values()))
    db_session.add(squad)
    db_session.flush()
    return squad, users


def _create_event(db_session, squad: Squad, creator: User, recipients: list[User]) -> RealtimeEvent:
    event = RealtimeEvent(
        squad_id=squad.id,
        payload={
            "type": "REFRESH_SQUAD",
            "action": "CREATE",
            "creator_id": creator.id,
            "push_recipients": [user.id for user in recipients],
        },
    )
    db_session.add(event)
    db_session.commit()
    return event


def _bind_relay(db_session, monkeypatch) -> None:
    monkeypatch.setattr(realtime_relay, "SessionLocal", sessionmaker(bind=db_session.get_bind()))


def test_transient_firebase_failure_is_retryable(monkeypatch):
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    monkeypatch.setattr(notifications.messaging, "send", Mock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        notifications.send_durable_push("token", "event")


def test_invalid_argument_is_a_request_rejection_not_a_dead_token(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(db_session, {"creator": "token_creator", "first": "token_first", "second": "token_second"})
    event = _create_event(db_session, squad, users["creator"], [users["first"], users["second"]])
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    monkeypatch.setattr(
        notifications.messaging,
        "send",
        Mock(side_effect=[exceptions.InvalidArgumentError("invalid request"), "accepted-message"]),
    )

    assert realtime_relay.dispatch_pushes(event.id) is True

    db_session.refresh(event)
    assert event.payload["push_completed"] == [users["second"].id]
    assert event.payload["push_rejected"] == {str(users["first"].id): "invalid_request"}
    assert users["first"].push_token == "token_first"
    assert notifications.messaging.send.call_count == 2


def test_transient_error_continues_and_resumes_only_pending_recipients(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(db_session, {"creator": "token_creator", "first": "token_first", "second": "token_second"})
    event = _create_event(db_session, squad, users["creator"], [users["first"], users["second"]])
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    sender = Mock(side_effect=[exceptions.InternalError("transient"), "accepted-message", "accepted-message"])
    monkeypatch.setattr(notifications.messaging, "send", sender)

    assert realtime_relay.dispatch_pushes(event.id) is False

    db_session.refresh(event)
    assert event.payload["push_completed"] == [users["second"].id]
    assert str(users["first"].id) not in event.payload.get("push_rejected", {})
    assert [call.args[0].token for call in sender.call_args_list] == ["token_first", "token_second"]

    assert realtime_relay.dispatch_pushes(event.id) is True

    db_session.refresh(event)
    assert event.payload["push_completed"] == [users["second"].id, users["first"].id]
    assert [call.args[0].token for call in sender.call_args_list] == [
        "token_first",
        "token_second",
        "token_first",
    ]


def test_unregistered_rejection_is_terminal_without_purging_token(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(db_session, {"creator": "token_creator", "gone": "token_gone", "second": "token_second"})
    event = _create_event(db_session, squad, users["creator"], [users["gone"], users["second"]])
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    monkeypatch.setattr(
        notifications.messaging,
        "send",
        Mock(side_effect=[messaging.UnregisteredError("gone"), "accepted-message"]),
    )

    assert realtime_relay.dispatch_pushes(event.id) is True

    db_session.refresh(event)
    assert event.payload["push_rejected"] == {str(users["gone"].id): "unregistered"}
    assert event.payload["push_completed"] == [users["second"].id]
    db_session.refresh(users["gone"])
    assert users["gone"].push_token == "token_gone"


def test_sender_id_mismatch_is_terminal_without_purging_token(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(db_session, {"creator": "token_creator", "mismatch": "token_mismatch"})
    event = _create_event(db_session, squad, users["creator"], [users["mismatch"]])
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    monkeypatch.setattr(
        notifications.messaging,
        "send",
        Mock(side_effect=messaging.SenderIdMismatchError("other sender")),
    )

    assert realtime_relay.dispatch_pushes(event.id) is True

    db_session.refresh(event)
    assert event.payload["push_rejected"] == {str(users["mismatch"].id): "sender_mismatch"}
    db_session.refresh(users["mismatch"])
    assert users["mismatch"].push_token == "token_mismatch"


def test_permission_denied_keeps_recipients_pending(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(db_session, {"creator": "token_creator", "denied": "token_denied", "second": "token_second"})
    event = _create_event(db_session, squad, users["creator"], [users["denied"], users["second"]])
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    monkeypatch.setattr(
        notifications.messaging,
        "send",
        Mock(side_effect=[exceptions.PermissionDeniedError("denied"), "accepted-message"]),
    )

    assert realtime_relay.dispatch_pushes(event.id) is False

    db_session.refresh(event)
    assert event.payload["push_completed"] == [users["second"].id]
    assert str(users["denied"].id) not in event.payload.get("push_rejected", {})

    notifications.messaging.send.side_effect = exceptions.PermissionDeniedError("denied")
    with pytest.raises(notifications.PushRetryableError) as info:
        notifications.send_durable_push("token_denied", "event")
    assert info.value.category == "authorization"


def test_firebase_disabled_keeps_event_pending(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(db_session, {"creator": "token_creator", "first": "token_first"})
    event = _create_event(db_session, squad, users["creator"], [users["first"]])
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", False)

    assert realtime_relay.dispatch_pushes(event.id) is False

    db_session.refresh(event)
    assert "push_completed" not in event.payload
    assert "push_rejected" not in event.payload


def test_creator_is_excluded_from_computed_recipients(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(
        db_session,
        {"creator": "token_creator", "first": "token_first", "second": "token_second"},
    )
    event = RealtimeEvent(
        squad_id=squad.id,
        payload={"type": "REFRESH_SQUAD", "action": "CREATE", "creator_id": users["creator"].id},
    )
    db_session.add(event)
    db_session.commit()
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    sender = Mock(return_value="accepted-message")
    monkeypatch.setattr(notifications.messaging, "send", sender)

    assert realtime_relay.dispatch_pushes(event.id) is True

    assert [call.args[0].token for call in sender.call_args_list].count("token_creator") == 0
    assert set(call.args[0].token for call in sender.call_args_list) == {"token_first", "token_second"}
    db_session.refresh(event)
    assert set(event.payload["push_recipients"]) == {users["first"].id, users["second"].id}


def test_member_without_token_is_terminal_and_does_not_block_others(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(db_session, {"creator": "token_creator", "left": None, "second": "token_second"})
    event = _create_event(db_session, squad, users["creator"], [users["left"], users["second"]])
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    sender = Mock(return_value="accepted-message")
    monkeypatch.setattr(notifications.messaging, "send", sender)

    assert realtime_relay.dispatch_pushes(event.id) is True

    db_session.refresh(event)
    assert event.payload["push_rejected"] == {str(users["left"].id): "no_token"}
    assert event.payload["push_completed"] == [users["second"].id]
    assert [call.args[0].token for call in sender.call_args_list] == ["token_second"]


def test_historical_push_completed_is_never_resent(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(
        db_session,
        {"creator": "token_creator", "first": "token_first", "second": "token_second", "third": "token_third"},
    )
    event = RealtimeEvent(
        squad_id=squad.id,
        payload={
            "type": "REFRESH_SQUAD",
            "action": "CREATE",
            "creator_id": users["creator"].id,
            "push_recipients": [users["first"].id, users["second"].id, users["third"].id],
            "push_completed": [users["first"].id, users["second"].id],
        },
    )
    db_session.add(event)
    db_session.commit()
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    sender = Mock(return_value="accepted-message")
    monkeypatch.setattr(notifications.messaging, "send", sender)

    assert realtime_relay.dispatch_pushes(event.id) is True

    assert [call.args[0].token for call in sender.call_args_list] == ["token_third"]
    db_session.refresh(event)
    assert event.payload["push_completed"] == [
        users["first"].id,
        users["second"].id,
        users["third"].id,
    ]


def test_unexpected_failure_does_not_log_token_or_stop_other_recipients(db_session, monkeypatch, caplog):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(
        db_session, {"creator": "token_creator", "first": "private-token-never-log", "second": "token_second"},
    )
    event = _create_event(db_session, squad, users["creator"], [users["first"], users["second"]])
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    sender = Mock(side_effect=[ValueError("Invalid token private-token-never-log"), "accepted-message"])
    monkeypatch.setattr(notifications.messaging, "send", sender)

    assert realtime_relay.dispatch_pushes(event.id) is False

    db_session.refresh(event)
    assert event.payload["push_completed"] == [users["second"].id]
    assert sender.call_count == 2
    assert "ValueError" in caplog.text
    assert "private-token-never-log" not in caplog.text


def test_departed_member_is_rejected_and_terminal_results_are_not_resent(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(
        db_session, {"creator": "token_creator", "left": "token_left", "second": "token_second"},
    )
    event = _create_event(db_session, squad, users["creator"], [users["left"], users["second"]])
    squad.members.remove(users["left"])
    db_session.commit()
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    sender = Mock(return_value="accepted-message")
    monkeypatch.setattr(notifications.messaging, "send", sender)

    assert realtime_relay.dispatch_pushes(event.id) is True
    db_session.refresh(event)
    assert event.payload["push_rejected"] == {str(users["left"].id): "not_member"}
    assert event.payload["push_completed"] == [users["second"].id]
    assert realtime_relay.dispatch_pushes(event.id) is True
    assert [call.args[0].token for call in sender.call_args_list] == ["token_second"]


def test_commit_failure_preserves_prior_results_and_does_not_acknowledge(db_session, monkeypatch):
    squad, users = _squad_with_members(
        db_session, {"creator": "token_creator", "first": "token_first", "second": "token_second"},
    )
    event = _create_event(db_session, squad, users["creator"], [users["first"], users["second"]])

    class FailSecondCommit(Session):
        commits = 0

        def commit(self) -> None:
            self.commits += 1
            if self.commits == 2:
                raise OperationalError("synthetic commit failure", None, RuntimeError("offline"))
            super().commit()

    monkeypatch.setattr(
        realtime_relay, "SessionLocal", sessionmaker(bind=db_session.get_bind(), class_=FailSecondCommit),
    )
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    sender = Mock(return_value="accepted-message")
    monkeypatch.setattr(notifications.messaging, "send", sender)

    with pytest.raises(OperationalError):
        realtime_relay.dispatch_pushes(event.id)

    db_session.refresh(event)
    assert event.dispatched_at is None
    assert event.payload["push_completed"] == [users["first"].id]
    _bind_relay(db_session, monkeypatch)
    assert realtime_relay.dispatch_pushes(event.id) is True
    # The second acceptance was not persisted: its retry is the documented
    # external-send/commit crash window, not a promise of exactly-once delivery.
    assert [call.args[0].token for call in sender.call_args_list] == ["token_first", "token_second", "token_second"]


@pytest.mark.asyncio
async def test_real_relay_retries_pending_then_acknowledges_without_private_fields(db_session, monkeypatch):
    _bind_relay(db_session, monkeypatch)
    squad, users = _squad_with_members(
        db_session, {"creator": "token_creator", "first": "token_first", "second": "token_second"},
    )
    event = _create_event(db_session, squad, users["creator"], [users["first"], users["second"]])
    event.payload = {**event.payload, "push_rejected": {"999": "not_member"}}
    db_session.commit()
    monkeypatch.setattr(notifications, "FIREBASE_ENABLED", True)
    sender = Mock(side_effect=[exceptions.InternalError("transient"), "accepted-message", "accepted-message"])
    monkeypatch.setattr(notifications.messaging, "send", sender)
    broadcasts = []
    quarantine = realtime_relay.QuarantineRegistry()

    for pass_index in range(2):
        stop = asyncio.Event()

        async def record_broadcast(squad_id, payload):
            broadcasts.append(dict(payload))
            stop.set()

        monkeypatch.setattr(realtime_relay.manager, "broadcast_to_squad", record_broadcast)
        await realtime_relay.realtime_relay_loop(stop, quarantine=quarantine)
        db_session.refresh(event)
        if pass_index == 0:
            assert event.dispatched_at is None
            assert event.payload["push_completed"] == [users["second"].id]
            assert quarantine._entries[event.id]["failures"] == 1
        else:
            assert event.dispatched_at is not None
            assert event.id not in quarantine._entries

    assert [call.args[0].token for call in sender.call_args_list] == ["token_first", "token_second", "token_first"]
    assert len(broadcasts) == 2
    assert all(not key.startswith("push_") for payload in broadcasts for key in payload)


@pytest.mark.asyncio
async def test_relay_does_not_acknowledge_event_with_pending_recipients(db_session, monkeypatch):
    squad, users = _squad_with_members(db_session, {"creator": "token_creator", "first": "token_first"})
    event = _create_event(db_session, squad, users["creator"], [users["first"]])
    _bind_relay(db_session, monkeypatch)
    stop = asyncio.Event()

    async def record_broadcast(squad_id, payload):
        stop.set()

    monkeypatch.setattr(realtime_relay.manager, "broadcast_to_squad", record_broadcast)
    monkeypatch.setattr(realtime_relay, "dispatch_pushes", Mock(return_value=False))
    await realtime_relay.realtime_relay_loop(stop)

    db_session.refresh(event)
    assert event.dispatched_at is None
