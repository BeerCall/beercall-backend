import os
import uuid
import time
import logging
import signal
import sys
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from db.database import SessionLocal, engine
from models.beer_call_job import BeerCallJob, BeerCallJobStatus
from services.beer_call_jobs import process_claimed_job

logger = logging.getLogger("beer_call_worker")
logging.basicConfig(level=logging.INFO)

shutdown_flag = False

def handle_sigterm(*args):
    global shutdown_flag
    logger.info("SIGTERM received, shutting down gracefully...")
    shutdown_flag = True

signal.signal(signal.SIGINT, handle_sigterm)
signal.signal(signal.SIGTERM, handle_sigterm)

def run_worker_loop():
    logger.info("BeerCall Worker started")
    while not shutdown_flag:
        job_processed = claim_and_process_job()
        
        # Purge orphelins tous les 100 tours ou arbitrairement
        if getattr(run_worker_loop, "loop_count", 0) % 50 == 0:
            purge_orphans()
            
        run_worker_loop.loop_count = getattr(run_worker_loop, "loop_count", 0) + 1
        
        if not job_processed:
            time.sleep(2)
            
    logger.info("Worker stopped")

def claim_and_process_job():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        timeout_threshold = now - timedelta(minutes=15)
        retry_delay_1 = now - timedelta(seconds=10)
        retry_delay_2 = now - timedelta(seconds=60)
        
        # Trouver un job PENDING (avec gestion du délai selon tentatives) ou RUNNING expiré
        job = db.query(BeerCallJob).with_for_update(skip_locked=True).filter(
            ((BeerCallJob.status == BeerCallJobStatus.PENDING) & (
                (BeerCallJob.attempts == 0) |
                ((BeerCallJob.attempts == 1) & (BeerCallJob.updated_at < retry_delay_1)) |
                ((BeerCallJob.attempts >= 2) & (BeerCallJob.updated_at < retry_delay_2))
            )) |
            ((BeerCallJob.status == BeerCallJobStatus.RUNNING) & (BeerCallJob.updated_at < timeout_threshold))
        ).order_by(BeerCallJob.created_at.asc()).first()
        
        if not job:
            return False

        owner_id = str(uuid.uuid4())
        job.status = BeerCallJobStatus.RUNNING
        job.owner_id = owner_id
        job.attempts += 1
        job.updated_at = now
        db.commit()
        
        job_id = job.id
    except Exception as e:
        logger.error(f"Error claiming job: {e}")
        db.rollback()
        return False
    finally:
        db.close()
        
    # Process
    try:
        process_claimed_job(lambda: SessionLocal(), job_id, owner_id)
    except Exception as e:
        logger.error(f"Error processing job {job_id}: {e}")
        
    return True

def purge_orphans():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(hours=1)
        
        # 1. Abandonnés (UPLOADING) : on supprime tout
        abandoned_jobs = db.query(BeerCallJob).filter(
            BeerCallJob.status == BeerCallJobStatus.UPLOADING,
            BeerCallJob.created_at < threshold
        ).all()
        
        for job in abandoned_jobs:
            input_path = f"uploads/jobs/{job.id}.input"
            tmp_path = f"uploads/jobs/{job.id}.tmp"
            if os.path.exists(input_path):
                os.remove(input_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            db.delete(job)

        # 2. Terminaux (REJECTED, FAILED) : on supprime les fichiers mais on conserve la DB
        terminal_jobs = db.query(BeerCallJob).filter(
            BeerCallJob.status.in_([BeerCallJobStatus.REJECTED, BeerCallJobStatus.FAILED]),
            BeerCallJob.updated_at < threshold
        ).all()
        
        for job in terminal_jobs:
            input_path = f"uploads/jobs/{job.id}.input"
            if os.path.exists(input_path):
                os.remove(input_path)
            
        db.commit()
    except Exception as e:
        logger.error(f"Error purging orphans: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    run_worker_loop()
