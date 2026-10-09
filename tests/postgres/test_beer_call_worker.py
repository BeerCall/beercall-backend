import pytest
import os
import uuid
import threading
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from models.user import User
from models.squad import Squad
from models.beer_call_job import BeerCallJob, BeerCallJobStatus
from workers.beer_call_worker import claim_and_process_job, purge_orphans
from unittest.mock import patch

@pytest.fixture
def setup_worker_data(db_session, postgres_engine):
    user = User(username="wkr2", hashed_password="pw")
    squad = Squad(name="wkr_sq", invite_code="WKRSQ")
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()
    
    # We clear dependency overrides in app if needed, but worker doesn't use FastAPI client.
    # It uses SessionLocal directly! 
    # For tests, we must mock SessionLocal so it points to the test DB!
    from db.database import SessionLocal
    # In db/database.py, engine is already pointing to test DB because of env var!
    
    return user, squad

def test_claim_and_process_job_concurrent(setup_worker_data, db_session):
    user, squad = setup_worker_data

    # On créé 1 job pending
    job = BeerCallJob(
        id=uuid.uuid4(),
        creator_id=user.id,
        squad_id=squad.id,
        idempotency_key="idemp_1",
        status=BeerCallJobStatus.PENDING,
        latitude=48.8,
        longitude=2.3,
        location_name="Lieu",
        attempts=0
    )
    db_session.add(job)
    
    # mock inputs
    os.makedirs("uploads/jobs", exist_ok=True)
    valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    with open(f"uploads/jobs/{job.id}.input", "wb") as f:
        f.write(valid_png)
        
    db_session.commit()

    processed_count = 0
    lock = threading.Lock()

    def run_worker():
        nonlocal processed_count
        while True:
            processed = claim_and_process_job()
            if processed:
                with lock:
                    processed_count += 1
            else:
                break

    with patch("services.beer_call_jobs.is_drink_detected", return_value=True):
        t1 = threading.Thread(target=run_worker)
        t2 = threading.Thread(target=run_worker)

        t1.start()
        t2.start()

        t1.join()
        t2.join()

    # 1 should succeed
    assert processed_count == 1
    db_session.expire_all()
    jobs = db_session.query(BeerCallJob).all()
    assert jobs[0].status == BeerCallJobStatus.SUCCEEDED


def test_purge_orphans(setup_worker_data, db_session):
    user, squad = setup_worker_data

    expired_job = BeerCallJob(
        id=uuid.uuid4(),
        creator_id=user.id,
        squad_id=squad.id,
        idempotency_key="orphan",
        status=BeerCallJobStatus.UPLOADING,
        latitude=48.8,
        longitude=2.3,
        location_name="Lieu",
        created_at=datetime.now(timezone.utc) - timedelta(hours=2)
    )
    db_session.add(expired_job)
    db_session.commit()

    os.makedirs("uploads/jobs", exist_ok=True)
    with open(f"uploads/jobs/{expired_job.id}.tmp", "w") as f:
        f.write("tmp")
        
    expired_job_id = expired_job.id

    purge_orphans()

    assert not os.path.exists(f"uploads/jobs/{expired_job_id}.tmp")
    db_session.expire_all()
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == expired_job_id).first()
    assert job is None
