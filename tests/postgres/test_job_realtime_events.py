import os
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, Mock, AsyncMock
import asyncio
from sqlalchemy.orm import Session, sessionmaker

from models.user import User
from models.squad import Squad
from models.beer_call_job import BeerCallJob, BeerCallJobStatus
from models.realtime_event import RealtimeEvent
from services.beer_call_jobs import process_claimed_job
from workers.beer_call_worker import claim_and_process_job
from core.realtime_relay import realtime_relay_loop


@pytest.fixture
def setup_job_data(postgres_engine, db_session):
    user = User(username=f"user_{uuid.uuid4().hex[:8]}", hashed_password="pw")
    squad = Squad(name=f"squad_{uuid.uuid4().hex[:8]}", invite_code=uuid.uuid4().hex[:6])
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()

    def make_job(status=BeerCallJobStatus.RUNNING, attempts=1, owner_id=None):
        job_id = uuid.uuid4()
        actual_owner = owner_id or str(uuid.uuid4())
        job = BeerCallJob(
            id=job_id,
            creator_id=user.id,
            squad_id=squad.id,
            idempotency_key=str(uuid.uuid4()),
            status=status,
            owner_id=actual_owner if status == BeerCallJobStatus.RUNNING else None,
            latitude=48.8566,
            longitude=2.3522,
            location_name="Paris",
            attempts=attempts
        )
        db_session.add(job)
        db_session.commit()

        os.makedirs("uploads/jobs", exist_ok=True)
        input_path = f"uploads/jobs/{job_id}.input"
        valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        with open(input_path, "wb") as f:
            f.write(valid_png)

        return job, actual_owner, input_path

    def db_factory():
        return Session(postgres_engine)

    yield user, squad, make_job, db_factory


def test_job_success_emits_refresh_squad_and_job_updated(setup_job_data, db_session):
    user, squad, make_job, db_factory = setup_job_data
    job, owner_id, input_path = make_job()

    with patch("services.beer_call_jobs.is_drink_detected", return_value=True):
        process_claimed_job(db_factory, job.id, owner_id)

    db_session.expire_all()
    reloaded_job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job.id).first()
    assert reloaded_job.status == BeerCallJobStatus.SUCCEEDED

    events = db_session.query(RealtimeEvent).filter(RealtimeEvent.squad_id == squad.id).all()
    # Pour réussite, présence du REFRESH_SQUAD inchangé ET celle du BEER_CALL_JOB_UPDATED
    refresh_squad_events = [e for e in events if e.payload.get("type") == "REFRESH_SQUAD"]
    job_updated_events = [e for e in events if e.payload.get("type") == "BEER_CALL_JOB_UPDATED"]

    assert len(refresh_squad_events) == 1
    create_event = refresh_squad_events[0]
    assert create_event.payload.get("action") == "CREATE"
    assert create_event.payload.get("creator_id") == user.id

    assert len(job_updated_events) == 1
    update_event = job_updated_events[0]
    assert update_event.payload == {
        "type": "BEER_CALL_JOB_UPDATED",
        "job_id": str(job.id)
    }


def test_job_reject_emits_refresh_squad_and_job_updated(setup_job_data, db_session):
    user, squad, make_job, db_factory = setup_job_data
    job, owner_id, input_path = make_job()

    with patch("services.beer_call_jobs.is_drink_detected", return_value=False):
        process_claimed_job(db_factory, job.id, owner_id)

    db_session.expire_all()
    reloaded_job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job.id).first()
    assert reloaded_job.status == BeerCallJobStatus.REJECTED

    events = db_session.query(RealtimeEvent).filter(RealtimeEvent.squad_id == squad.id).all()
    refresh_squad_events = [e for e in events if e.payload.get("type") == "REFRESH_SQUAD"]
    job_updated_events = [e for e in events if e.payload.get("type") == "BEER_CALL_JOB_UPDATED"]

    assert len(refresh_squad_events) == 1
    assert refresh_squad_events[0].payload.get("action") == "REJECTED"

    assert len(job_updated_events) == 1
    assert job_updated_events[0].payload == {
        "type": "BEER_CALL_JOB_UPDATED",
        "job_id": str(job.id)
    }


def test_job_service_definitive_failure_emits_job_updated(setup_job_data, db_session):
    user, squad, make_job, db_factory = setup_job_data
    job, owner_id, input_path = make_job(attempts=3)

    with patch("services.beer_call_jobs.is_drink_detected", side_effect=Exception("Crash")):
        process_claimed_job(db_factory, job.id, owner_id)

    db_session.expire_all()
    reloaded_job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job.id).first()
    assert reloaded_job.status == BeerCallJobStatus.FAILED

    events = db_session.query(RealtimeEvent).filter(RealtimeEvent.squad_id == squad.id).all()
    refresh_squad_events = [e for e in events if e.payload.get("type") == "REFRESH_SQUAD"]
    job_updated_events = [e for e in events if e.payload.get("type") == "BEER_CALL_JOB_UPDATED"]

    assert len(refresh_squad_events) == 0
    assert len(job_updated_events) == 1
    assert job_updated_events[0].payload == {
        "type": "BEER_CALL_JOB_UPDATED",
        "job_id": str(job.id)
    }


def test_job_worker_exhaustion_emits_job_updated(setup_job_data, db_session, monkeypatch):
    user, squad, make_job, db_factory = setup_job_data
    job, _, _ = make_job(status=BeerCallJobStatus.PENDING, attempts=3)

    from workers import beer_call_worker
    monkeypatch.setattr(beer_call_worker, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

    processed = claim_and_process_job()
    assert processed is True

    db_session.expire_all()
    reloaded_job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job.id).first()
    assert reloaded_job.status == BeerCallJobStatus.FAILED
    assert reloaded_job.owner_id is None

    events = db_session.query(RealtimeEvent).filter(RealtimeEvent.squad_id == squad.id).all()
    assert len(events) == 1
    assert events[0].payload == {
        "type": "BEER_CALL_JOB_UPDATED",
        "job_id": str(job.id)
    }


def test_no_event_on_lease_lost(setup_job_data, db_session):
    user, squad, make_job, db_factory = setup_job_data
    job, owner_id, _ = make_job()

    wrong_owner_id = str(uuid.uuid4())
    with patch("services.beer_call_jobs.is_drink_detected", return_value=True):
        process_claimed_job(db_factory, job.id, wrong_owner_id)

    db_session.expire_all()
    events = db_session.query(RealtimeEvent).filter(RealtimeEvent.squad_id == squad.id).all()
    assert len(events) == 0


def test_no_event_on_retryable_error_return_pending(setup_job_data, db_session):
    user, squad, make_job, db_factory = setup_job_data
    job, owner_id, _ = make_job(attempts=1)

    with patch("services.beer_call_jobs.is_drink_detected", side_effect=RuntimeError("Transient")):
        process_claimed_job(db_factory, job.id, owner_id)

    db_session.expire_all()
    reloaded_job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job.id).first()
    assert reloaded_job.status == BeerCallJobStatus.PENDING
    assert reloaded_job.owner_id is None

    events = db_session.query(RealtimeEvent).filter(RealtimeEvent.squad_id == squad.id).all()
    assert len(events) == 0


def test_no_event_on_rollback(setup_job_data, db_session, monkeypatch):
    user, squad, make_job, db_factory = setup_job_data
    job, owner_id, _ = make_job()

    def fake_commit_fail(*args, **kwargs):
        raise RuntimeError("DB Disconnected during commit")

    with patch("services.beer_call_jobs.is_drink_detected", return_value=True):
        with patch.object(Session, "commit", side_effect=fake_commit_fail):
            process_claimed_job(db_factory, job.id, owner_id)

    db_session.expire_all()
    events = db_session.query(RealtimeEvent).filter(RealtimeEvent.squad_id == squad.id).all()
    assert len(events) == 0


@pytest.mark.asyncio
async def test_relay_dispatches_pushes_for_create_and_broadcasts_job_updated(setup_job_data, db_session, monkeypatch):
    user, squad, make_job, db_factory = setup_job_data
    job, owner_id, _ = make_job()

    with patch("services.beer_call_jobs.is_drink_detected", return_value=True):
        process_claimed_job(db_factory, job.id, owner_id)

    # Isolated relay setup
    monkeypatch.setattr("core.realtime_relay.SessionLocal", sessionmaker(bind=db_session.get_bind()))

    broadcast_mock = AsyncMock()
    monkeypatch.setattr("core.realtime_relay.manager.broadcast_to_squad", broadcast_mock)

    dispatch_pushes_mock = Mock()
    monkeypatch.setattr("core.realtime_relay.dispatch_pushes", dispatch_pushes_mock)

    stop_event = asyncio.Event()

    async def run_one_cycle():
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.gather(
        realtime_relay_loop(stop_event),
        run_one_cycle()
    )

    # Check broadcast was called for both events
    broadcast_payloads = [call[0][1] for call in broadcast_mock.call_args_list]
    payload_types = [p.get("type") for p in broadcast_payloads]
    assert "REFRESH_SQUAD" in payload_types
    assert "BEER_CALL_JOB_UPDATED" in payload_types

    # Check dispatch_pushes was called EXACTLY ONCE for REFRESH_SQUAD with action CREATE
    assert dispatch_pushes_mock.call_count == 1

    # Verify both events are now marked dispatched
    db_session.expire_all()
    events = db_session.query(RealtimeEvent).filter(RealtimeEvent.squad_id == squad.id).all()
    assert len(events) == 2
    assert all(e.dispatched_at is not None for e in events)
