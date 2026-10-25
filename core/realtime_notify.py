"""Transactional PostgreSQL outbox notifier for BeerCall.

Installs an idempotent after_flush hook on sqlalchemy.orm.Session that emits
a pg_notify on the 'beercall_realtime' channel within the active transaction
whenever new RealtimeEvent entities are flushed.
"""
from typing import Optional
from sqlalchemy import event, text
from sqlalchemy.orm import Session

REALTIME_CHANNEL = "beercall_realtime"
_is_installed = False


def register_realtime_outbox_notifier() -> None:
    """Install the transactional pg_notify hook on sqlalchemy.orm.Session.

    Idempotent: registers at most once across the process lifecycle.
    """
    global _is_installed
    if _is_installed:
        return

    if not event.contains(Session, "after_flush", _after_flush_notify):
        event.listen(Session, "after_flush", _after_flush_notify)
    _is_installed = True


def _after_flush_notify(session: Session, flush_context) -> None:
    """SQLAlchemy after_flush hook:
    If dialect is postgresql and new RealtimeEvent instances were inserted during flush,
    executes SELECT pg_notify('beercall_realtime', '') using the session's active connection.
    No-op on SQLite. Rollback cancels notification automatically via PostgreSQL transactional semantics.
    """
    try:
        bind = session.get_bind()
    except Exception:
        return

    if bind is None or bind.dialect.name != "postgresql":
        return

    # Delay model import to prevent circular dependencies
    from models.realtime_event import RealtimeEvent

    has_new_events = any(isinstance(obj, RealtimeEvent) for obj in session.new)
    if not has_new_events:
        return

    connection = session.connection()
    connection.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": REALTIME_CHANNEL, "payload": ""},
    )
