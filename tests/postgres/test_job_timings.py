import os
import json
import uuid
from unittest.mock import patch
import pytest

from models.user import User
from models.squad import Squad
from models.beer_call_job import BeerCallJob, BeerCallJobStatus
from services.beer_call_jobs import enqueue_beer_call_job, process_claimed_job
from workers.beer_call_worker import claim_and_process_job


@pytest.fixture
def setup_timing_data(db_session, postgres_engine):
    user = User(username=f"timing_{uuid.uuid4().hex[:6]}", hashed_password="pw")
    squad = Squad(name="timing_squad", invite_code=uuid.uuid4().hex[:5].upper())
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()
    return user, squad


VALID_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def test_job_timings_full_success_lifecycle(setup_timing_data, db_session, caplog):
    user, squad = setup_timing_data
    idemp = str(uuid.uuid4())

    with caplog.at_level("INFO", logger="beer_job_timing"):
        res = enqueue_beer_call_job(
            db=db_session,
            creator_id=user.id,
            squad_id=squad.id,
            idempotency_key=idemp,
            latitude=48.8566,
            longitude=2.3522,
            location_name="Bar Timing",
            file_bytes=VALID_PNG,
            file_extension="png",
        )
        job_id = res["job_id"]

        with patch("services.beer_call_jobs.is_drink_detected", return_value=True):
            processed = claim_and_process_job()
            assert processed is True

    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == "beer_job_timing"]
    phases = [r["phase"] for r in records if r.get("job_id") == job_id]

    assert "enqueued" in phases
    assert "claimed" in phases
    assert "file_read" in phases
    assert "detector_total" in phases
    assert "finalization" in phases
    assert "result" in phases

    by_phase = {r["phase"]: r for r in records if r.get("job_id") == job_id}

    enq = by_phase["enqueued"]
    assert enq["attempt"] == 0
    assert enq["status"] == "pending"
    assert enq["pid"] > 0
    assert "utc" in enq

    clm = by_phase["claimed"]
    assert clm["attempt"] == 1
    assert clm["status"] == "running"
    assert clm["pid"] > 0

    read = by_phase["file_read"]
    assert read["status"] == "success"
    assert read["duration_ms"] >= 0

    det = by_phase["detector_total"]
    assert det["status"] == "success"
    assert det["duration_ms"] >= 0

    fin = by_phase["finalization"]
    assert fin["status"] == "success"
    assert fin["duration_ms"] >= 0

    res_event = by_phase["result"]
    assert res_event["outcome"] == "succeeded"
    assert res_event["status"] == "success"


def test_job_timings_rejected_lifecycle(setup_timing_data, db_session, caplog):
    user, squad = setup_timing_data
    idemp = str(uuid.uuid4())

    with caplog.at_level("INFO", logger="beer_job_timing"):
        res = enqueue_beer_call_job(
            db=db_session,
            creator_id=user.id,
            squad_id=squad.id,
            idempotency_key=idemp,
            latitude=48.8566,
            longitude=2.3522,
            location_name="Bar Fraude",
            file_bytes=VALID_PNG,
            file_extension="png",
        )
        job_id = res["job_id"]

        with patch("services.beer_call_jobs.is_drink_detected", return_value=False):
            processed = claim_and_process_job()
            assert processed is True

    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == "beer_job_timing"]
    by_phase = {r["phase"]: r for r in records if r.get("job_id") == job_id}

    det = by_phase["detector_total"]
    assert det["status"] == "rejected"
    assert det["duration_ms"] >= 0

    fin = by_phase["finalization"]
    assert fin["status"] == "success"

    res_event = by_phase["result"]
    assert res_event["outcome"] == "rejected"
    assert res_event["status"] == "success"


def test_job_timings_retry_lifecycle_does_not_emit_false_success(setup_timing_data, db_session, caplog):
    user, squad = setup_timing_data
    idemp = str(uuid.uuid4())

    with caplog.at_level("INFO", logger="beer_job_timing"):
        res = enqueue_beer_call_job(
            db=db_session,
            creator_id=user.id,
            squad_id=squad.id,
            idempotency_key=idemp,
            latitude=48.8566,
            longitude=2.3522,
            location_name="Bar Retry",
            file_bytes=VALID_PNG,
            file_extension="png",
        )
        job_id = res["job_id"]

        with patch("services.beer_call_jobs.is_drink_detected", side_effect=RuntimeError("AI Timeout")):
            processed = claim_and_process_job()
            assert processed is True

    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == "beer_job_timing"]
    by_phase = {r["phase"]: r for r in records if r.get("job_id") == job_id}

    det = by_phase["detector_total"]
    assert det["status"] == "error"
    assert det["error"] == "RuntimeError"

    fin = by_phase["finalization"]
    assert fin["status"] == "success"

    res_event = by_phase["result"]
    assert res_event["outcome"] == "pending"


def test_job_timings_finalization_failure(setup_timing_data, db_session, caplog, postgres_engine):
    user, squad = setup_timing_data
    job_id = uuid.uuid4()
    owner_id = str(uuid.uuid4())

    job = BeerCallJob(
        id=job_id,
        creator_id=user.id,
        squad_id=squad.id,
        idempotency_key=str(uuid.uuid4()),
        status=BeerCallJobStatus.RUNNING,
        owner_id=owner_id,
        latitude=48.8,
        longitude=2.3,
        location_name="Bar Fail",
        attempts=1,
    )
    db_session.add(job)
    db_session.commit()

    os.makedirs("uploads/jobs", exist_ok=True)
    with open(f"uploads/jobs/{job.id}.input", "wb") as f:
        f.write(VALID_PNG)

    from sqlalchemy.orm import Session

    def db_factory():
        s = Session(postgres_engine)
        # Monkeypatch commit on the second session (the finalization session)
        orig_commit = s.commit

        def failing_commit():
            raise RuntimeError("DB Crash on commit")

        s.commit = failing_commit
        return s

    with caplog.at_level("INFO", logger="beer_job_timing"):
        with patch("services.beer_call_jobs.is_drink_detected", return_value=True):
            process_claimed_job(db_factory, job_id, owner_id)

    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == "beer_job_timing"]
    by_phase = {r["phase"]: r for r in records if r.get("job_id") == str(job_id)}

    assert "finalization" in by_phase
    assert by_phase["finalization"]["status"] == "error"
    assert by_phase["finalization"]["error"] == "RuntimeError"
    # S'assurer qu'aucun événement result ne prétend un succès
    assert "result" not in by_phase

    if os.path.exists(f"uploads/jobs/{job.id}.input"):
        os.remove(f"uploads/jobs/{job.id}.input")
