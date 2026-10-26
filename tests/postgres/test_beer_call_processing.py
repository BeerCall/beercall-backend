import pytest
import uuid
import os
from sqlalchemy.orm import Session
from models.user import User
from models.squad import Squad
from models.beer_call_job import BeerCallJob, BeerCallJobStatus
from services.beer_call_jobs import process_claimed_job

@pytest.fixture
def setup_job(postgres_engine, db_session):
    user = User(username="worker_user", hashed_password="pw")
    squad = Squad(name="worker_squad", invite_code="WRK")
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()

    job_id = uuid.uuid4()
    owner_id = str(uuid.uuid4())
    job = BeerCallJob(
        id=job_id,
        creator_id=user.id,
        squad_id=squad.id,
        idempotency_key="idemp",
        status=BeerCallJobStatus.RUNNING,
        owner_id=owner_id,
        latitude=48.8566,
        longitude=2.3522,
        location_name="Paris",
        attempts=1
    )
    db_session.add(job)
    db_session.commit()

    os.makedirs("uploads/jobs", exist_ok=True)
    input_path = f"uploads/jobs/{job_id}.input"
    valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    with open(input_path, "wb") as f:
        f.write(valid_png)

    def db_factory():
        from sqlalchemy.orm import Session
        return Session(postgres_engine)

    yield job_id, owner_id, db_factory

    if os.path.exists(input_path):
        os.remove(input_path)

def test_process_claimed_job_success(setup_job, db_session):
    from unittest.mock import patch
    job_id, owner_id, db_factory = setup_job
    
    with patch("services.beer_call_jobs.is_drink_detected", return_value=True):
        process_claimed_job(db_factory, job_id, owner_id)
        
    db_session.expire_all()
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()
    assert job.status == BeerCallJobStatus.SUCCEEDED

def test_process_claimed_job_reject(setup_job, db_session):
    from unittest.mock import patch
    job_id, owner_id, db_factory = setup_job
    
    with patch("services.beer_call_jobs.is_drink_detected", return_value=False):
        process_claimed_job(db_factory, job_id, owner_id)
        
    db_session.expire_all()
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()
    assert job.status == BeerCallJobStatus.REJECTED

def test_process_claimed_job_retryable_error(setup_job, db_session):
    from unittest.mock import patch
    job_id, owner_id, db_factory = setup_job
    
    with patch("services.beer_call_jobs.is_drink_detected", side_effect=RuntimeError("AI Error")):
        process_claimed_job(db_factory, job_id, owner_id)
        
    db_session.expire_all()
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()
    assert job.status == BeerCallJobStatus.PENDING
    assert job.owner_id is None

import uuid as _uuid
from unittest.mock import Mock, patch

from models.apero import Apero


def _set_attempts(db_session, job_id, attempts):
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()
    job.attempts = attempts
    db_session.commit()
    return job


def test_process_claimed_job_missing_photo_requeues_without_detector(setup_job, db_session):
    job_id, owner_id, db_factory = setup_job
    _set_attempts(db_session, job_id, 1)
    os.remove(f"uploads/jobs/{job_id}.input")

    detector = Mock(return_value=True)
    with patch("services.beer_call_jobs.is_drink_detected", detector):
        process_claimed_job(db_factory, job_id, owner_id)

    db_session.expire_all()
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()
    assert job.status == BeerCallJobStatus.PENDING
    assert job.owner_id is None
    assert detector.call_count == 0
    assert db_session.query(Apero).filter(Apero.source_job_id == job_id).count() == 0


def test_process_claimed_job_missing_photo_fails_at_attempt_cap(setup_job, db_session):
    job_id, owner_id, db_factory = setup_job
    _set_attempts(db_session, job_id, 3)
    os.remove(f"uploads/jobs/{job_id}.input")

    detector = Mock(return_value=True)
    with patch("services.beer_call_jobs.is_drink_detected", detector):
        process_claimed_job(db_factory, job_id, owner_id)

    db_session.expire_all()
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()
    assert job.status == BeerCallJobStatus.FAILED
    assert detector.call_count == 0
    assert db_session.query(Apero).filter(Apero.source_job_id == job_id).count() == 0


def test_process_claimed_job_permission_error_requeues_without_detector(setup_job, db_session):
    job_id, owner_id, db_factory = setup_job
    _set_attempts(db_session, job_id, 2)

    detector = Mock(return_value=True)
    with patch("services.beer_call_jobs.is_drink_detected", detector):
        with patch("builtins.open", side_effect=PermissionError("refusé")):
            process_claimed_job(db_factory, job_id, owner_id)

    db_session.expire_all()
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()
    assert job.status == BeerCallJobStatus.PENDING
    assert job.owner_id is None
    assert detector.call_count == 0
    assert db_session.query(Apero).filter(Apero.source_job_id == job_id).count() == 0
