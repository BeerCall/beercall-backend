from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from models.apero import Apero
from models.beer_call_job import BeerCallJob, BeerCallJobStatus
from models.squad import Squad
from models.user import User
from services.beer_call_jobs import process_claimed_job
from workers import beer_call_worker


@pytest.fixture
def contract_data(db_session, postgres_engine, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    factory = sessionmaker(bind=postgres_engine)
    monkeypatch.setattr(beer_call_worker, "SessionLocal", factory)
    users = [User(username=f"contract-{i}", hashed_password="unused") for i in range(2)]
    squads = [Squad(name=f"Contract {i}", invite_code=f"contract-{i}") for i in range(2)]
    db_session.add_all(users + squads)
    db_session.commit()
    Path("uploads/jobs").mkdir(parents=True)
    return users, squads, factory


def make_job(db, user, squad, **kwargs):
    job = BeerCallJob(id=uuid4(), creator_id=user.id, squad_id=squad.id,
                      idempotency_key=str(uuid4()), latitude=48.8, longitude=2.3,
                      location_name="Contract", **kwargs)
    db.add(job)
    db.commit()
    Path(f"uploads/jobs/{job.id}.input").write_bytes(b"mocked image")
    return job


@pytest.mark.parametrize("same_creator", [False, True])
def test_distinct_jobs_cannot_duplicate_business_creation(contract_data, db_session, monkeypatch, same_creator):
    users, squads, factory = contract_data
    jobs = [make_job(db_session, users[0], squads[0], status=BeerCallJobStatus.RUNNING,
                     attempts=1, owner_id=str(uuid4())),
            make_job(db_session, users[0] if same_creator else users[1],
                     squads[1] if same_creator else squads[0],
                     status=BeerCallJobStatus.RUNNING, attempts=1, owner_id=str(uuid4()))]
    identities = [(job.id, job.owner_id) for job in jobs]
    barrier = Barrier(2)
    def infer(_):
        barrier.wait(timeout=10)
        return True
    monkeypatch.setattr("services.beer_call_jobs.is_drink_detected", infer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(process_claimed_job, factory, job_id, owner) for job_id, owner in identities]
        for future in futures:
            future.result(timeout=20)
    db_session.expire_all()
    statuses = [db_session.get(BeerCallJob, job_id).status for job_id, _ in identities]
    assert statuses.count(BeerCallJobStatus.SUCCEEDED) == 1
    assert statuses.count(BeerCallJobStatus.REJECTED) == 1
    assert db_session.query(Apero).count() == 1


def test_retry_delays_and_global_attempt_limit(contract_data, db_session, monkeypatch):
    users, squads, _ = contract_data
    job = make_job(db_session, users[0], squads[0], status=BeerCallJobStatus.PENDING, attempts=0)
    def fail(_):
        raise RuntimeError("AI unavailable")
    monkeypatch.setattr("services.beer_call_jobs.is_drink_detected", fail)
    for attempt, delay in [(1, 11), (2, 61)]:
        assert beer_call_worker.claim_and_process_job()
        db_session.refresh(job)
        assert job.attempts == attempt
        assert job.status == BeerCallJobStatus.PENDING
        assert not beer_call_worker.claim_and_process_job()
        job.updated_at = datetime.now(timezone.utc) - timedelta(seconds=delay)
        db_session.commit()
    assert beer_call_worker.claim_and_process_job()
    db_session.refresh(job)
    assert job.attempts == 3
    assert job.status == BeerCallJobStatus.FAILED
    assert not beer_call_worker.claim_and_process_job()


def test_expired_third_lease_is_terminal(contract_data, db_session):
    users, squads, _ = contract_data
    job = make_job(db_session, users[0], squads[0], status=BeerCallJobStatus.RUNNING,
                   owner_id=str(uuid4()), attempts=3,
                   updated_at=datetime.now(timezone.utc) - timedelta(minutes=16))
    assert beer_call_worker.claim_and_process_job()
    db_session.refresh(job)
    assert job.status == BeerCallJobStatus.FAILED
    assert job.attempts == 3
    assert job.owner_id is None


def test_purge_preserves_accepted_pending_job_and_input(contract_data, db_session):
    users, squads, _ = contract_data
    job = make_job(db_session, users[0], squads[0], status=BeerCallJobStatus.PENDING,
                   attempts=0, created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    beer_call_worker.purge_orphans()
    db_session.refresh(job)
    assert job.status == BeerCallJobStatus.PENDING
    assert Path(f"uploads/jobs/{job.id}.input").exists()
