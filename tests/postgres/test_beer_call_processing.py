import json
import os
import uuid
from contextlib import nullcontext
from typing import Callable

import pytest
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


def _run_photo_read_failure(
    setup_job: tuple[uuid.UUID, str, Callable[[], Session]],
    db_session: Session,
    attempts: int,
    error: type[Exception],
    caplog: pytest.LogCaptureFixture,
) -> BeerCallJob:
    """Exécute le vrai service sur une erreur de lecture et prouve tous ses effets.

    Le service n'est pas mocké : seules la lecture fichier et les effets externes
    (détecteur, récompenses, fraude) sont remplacés, pour observer la finalisation réelle.
    """
    job_id, owner_id, db_factory = setup_job
    _set_attempts(db_session, job_id, attempts)
    expected_status = (
        BeerCallJobStatus.FAILED if attempts >= 3 else BeerCallJobStatus.PENDING
    )

    detector = Mock(return_value=True)
    rewards = Mock()
    fraud = Mock()
    closed_sessions: list[Session] = []
    original_close = Session.close

    def record_close(session: Session) -> None:
        closed_sessions.append(session)
        original_close(session)

    input_path = f"uploads/jobs/{job_id}.input"
    if error is FileNotFoundError:
        os.remove(input_path)
        read_patch = nullcontext()
    else:
        read_patch = patch("builtins.open", side_effect=error("lecture refusée"))

    with caplog.at_level("INFO", logger="beer_job_timing"), \
            patch("services.beer_call_jobs.is_drink_detected", detector), \
            patch("services.beer_call_jobs.apply_beer_call_creation_rewards", rewards), \
            patch("services.beer_call_jobs.handle_ia_fraud", fraud), \
            patch.object(Session, "close", record_close), \
            read_patch:
        process_claimed_job(db_factory, job_id, owner_id)

    db_session.expire_all()
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()

    assert job.status == expected_status
    if expected_status == BeerCallJobStatus.PENDING:
        assert job.owner_id is None
    else:
        # Au plafond la sémantique actuelle conserve le lease : aucun retry n'est planifié.
        assert job.owner_id == owner_id

    assert detector.call_count == 0
    assert rewards.call_count == 0
    assert fraud.call_count == 0
    assert db_session.query(Apero).filter(Apero.source_job_id == job_id).count() == 0
    assert len(closed_sessions) == 2, "les deux sessions du service doivent être fermées"

    records = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "beer_job_timing"
    ]
    by_phase = {r["phase"]: r for r in records if r.get("job_id") == str(job_id)}

    read = by_phase["file_read"]
    assert read["status"] == "error"
    assert read["error"] == error.__name__
    assert read["attempt"] == attempts
    assert read["duration_ms"] >= 0
    assert "detector_total" not in by_phase
    assert by_phase["finalization"]["status"] == "success"
    assert by_phase["result"]["outcome"] == expected_status.value
    assert by_phase["result"]["status"] == "success"

    return job


def test_process_claimed_job_file_not_found_attempt_2_requeues(
    setup_job, db_session, caplog
):
    job = _run_photo_read_failure(setup_job, db_session, 2, FileNotFoundError, caplog)
    assert job.status == BeerCallJobStatus.PENDING


def test_process_claimed_job_permission_error_attempt_1_requeues(
    setup_job, db_session, caplog
):
    job = _run_photo_read_failure(setup_job, db_session, 1, PermissionError, caplog)
    assert job.status == BeerCallJobStatus.PENDING


def test_process_claimed_job_permission_error_attempt_3_fails(
    setup_job, db_session, caplog
):
    job = _run_photo_read_failure(setup_job, db_session, 3, PermissionError, caplog)
    assert job.status == BeerCallJobStatus.FAILED
