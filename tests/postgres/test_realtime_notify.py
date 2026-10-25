"""Tests for transactional PostgreSQL outbox notification (T28).

Verifies:
- LISTEN connection receives nothing before commit
- LISTEN connection receives nothing after rollback
- LISTEN connection receives empty notification on 'beercall_realtime' after commit
- Independent worker sessions (distinct sessionmaker) are covered by the Session hook
- Updating existing events (e.g. dispatched_at) triggers no notification
- Double installation is strictly idempotent and does not produce duplicate callbacks
- SQLite execution is a clean no-op
"""
from datetime import datetime, timezone
import pytest
import psycopg
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.realtime_notify import REALTIME_CHANNEL, register_realtime_outbox_notifier
from db.database import Base
from models.realtime_event import RealtimeEvent
from models.squad import Squad


def get_libpq_uri(postgres_url: str) -> str:
    """Convert SQLAlchemy connection URL to standard libpq connection URI for psycopg."""
    return postgres_url.replace("postgresql+psycopg://", "postgresql://")


def test_postgresql_transactional_notification_lifecycle(postgres_url, postgres_engine):
    """Verify that PostgreSQL outbox notifications are strictly transactional:
    - Nothing received before commit
    - Nothing received after rollback
    - Exactly one notification received after commit
    - Worker sessions covered
    - No notification on update
    - Double installation idempotent without duplicate callback
    """
    conn_uri = get_libpq_uri(postgres_url)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=postgres_engine)

    with Session() as session:
        squad = Squad(name="Notify Lifecycle Squad", icon="🍺", color="#112233", invite_code="NOTIFY_LIFE")
        session.add(squad)
        session.commit()
        squad_id = squad.id

    with psycopg.connect(conn_uri, autocommit=True) as listen_conn:
        listen_conn.execute(f"LISTEN {REALTIME_CHANNEL};")

        # 1. LISTEN receives nothing before commit
        with Session() as session:
            ev_uncommitted = RealtimeEvent(squad_id=squad_id, payload={"type": "BEFORE_COMMIT"})
            session.add(ev_uncommitted)
            session.flush()

            # Listener should not see any notification while transaction is uncommitted
            received = list(listen_conn.notifies(timeout=0.2))
            assert len(received) == 0, "Notification leaked before transaction commit!"

            # 2. Rollback cancels the notification
            session.rollback()

        received = list(listen_conn.notifies(timeout=0.2))
        assert len(received) == 0, "Notification delivered despite transaction rollback!"

        # 3. Exactly one notification delivered after commit of a real event
        with Session() as session:
            ev_committed = RealtimeEvent(squad_id=squad_id, payload={"type": "AFTER_COMMIT"})
            session.add(ev_committed)
            session.commit()

        received = list(listen_conn.notifies(timeout=1.0, stop_after=1))
        assert len(received) == 1, f"Expected 1 notification after commit, got {len(received)}"
        assert received[0].channel == REALTIME_CHANNEL
        assert received[0].payload == ""

        # 4. Insert via independent worker sessionmaker is covered
        WorkerSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=postgres_engine)
        with WorkerSessionLocal() as worker_session:
            ev_worker = RealtimeEvent(squad_id=squad_id, payload={"type": "WORKER_INSERT"})
            worker_session.add(ev_worker)
            worker_session.commit()
            worker_ev_id = ev_worker.id

        received = list(listen_conn.notifies(timeout=1.0, stop_after=1))
        assert len(received) == 1, f"Worker session did not trigger notification, got {len(received)}"
        assert received[0].channel == REALTIME_CHANNEL
        assert received[0].payload == ""

        # 5. Updates to existing events (e.g. dispatched_at) emit no notification
        with Session() as session:
            ev_to_update = session.query(RealtimeEvent).filter(RealtimeEvent.id == worker_ev_id).first()
            assert ev_to_update is not None
            ev_to_update.dispatched_at = datetime.now(timezone.utc)
            session.commit()

        received = list(listen_conn.notifies(timeout=0.2))
        assert len(received) == 0, "Update operation incorrectly triggered a realtime notification!"

        # 6. Double registration is idempotent and does not produce duplicate callbacks
        register_realtime_outbox_notifier()
        register_realtime_outbox_notifier()

        with Session() as session:
            ev_idempotent = RealtimeEvent(squad_id=squad_id, payload={"type": "IDEMPOTENT_CHECK"})
            session.add(ev_idempotent)
            session.commit()

        received = list(listen_conn.notifies(timeout=0.5))
        assert len(received) == 1, f"Expected exactly 1 notification after double registration, got {len(received)}"
        assert received[0].channel == REALTIME_CHANNEL


def test_sqlite_realtime_notify_noop(db_session):
    """Verify that on SQLite, inserting RealtimeEvent is a clean no-op
    without attempting to execute pg_notify or raising dialect errors.
    """
    bind = db_session.get_bind()
    # Ensure hook is active
    register_realtime_outbox_notifier()

    squad = Squad(name="SQLite Notify Squad", icon="🍻", color="#223344", invite_code="SQLITE_NOTIF")
    db_session.add(squad)
    db_session.commit()

    ev = RealtimeEvent(squad_id=squad.id, payload={"platform": "sqlite", "action": "noop_check"})
    db_session.add(ev)
    # Must flush and commit smoothly without error
    db_session.commit()

    fetched = db_session.query(RealtimeEvent).filter(RealtimeEvent.id == ev.id).first()
    assert fetched is not None
    assert fetched.payload["action"] == "noop_check"
