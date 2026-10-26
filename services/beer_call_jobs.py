import os
import uuid
import json
import time
import logging
import shutil
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from models.beer_call_job import BeerCallJob, BeerCallJobStatus
from models.realtime_event import RealtimeEvent
from models.apero import Apero, AperoStatus, AperoParticipant, ParticipationStatus
from models.squad import Squad
from models.user import User
from datetime import datetime, timezone, timedelta
from services.photo_validation import calculate_geodistance, is_drink_detected
from services.gamification import handle_ia_fraud, apply_beer_call_creation_rewards

logger = logging.getLogger(__name__)
job_timing_logger = logging.getLogger("beer_job_timing")


def log_job_timing(
    phase: str,
    job_id: uuid.UUID | str,
    pid: int,
    attempt: int,
    duration_ms: Optional[float] = None,
    status: Optional[str] = None,
    outcome: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    utc_str = datetime.now(timezone.utc).isoformat()
    payload = {
        "event": "beer_job_timing",
        "phase": phase,
        "job_id": str(job_id),
        "pid": pid,
        "attempt": attempt,
        "utc": utc_str,
    }
    if duration_ms is not None:
        payload["duration_ms"] = round(max(0.0, duration_ms), 2)
    if status is not None:
        payload["status"] = status
    if outcome is not None:
        payload["outcome"] = outcome
    if error is not None:
        payload["error"] = error
    job_timing_logger.info(json.dumps(payload), extra=payload)



class InvalidIdempotencyKey(ValueError):
    """The public idempotency key is not a UUID."""


class IdempotencyConflict(ValueError):
    """An existing key identifies a different request."""

def enqueue_beer_call_job(
    db: Session,
    creator_id: int,
    squad_id: int,
    idempotency_key: str,
    latitude: float,
    longitude: float,
    location_name: str,
    file_bytes: bytes,
    file_extension: str
) -> dict:
    # 0. Valider le format de l'Idempotency-Key
    try:
        idempotency_key = str(uuid.UUID(idempotency_key))
    except ValueError as exc:
        raise InvalidIdempotencyKey("Format Idempotency-Key invalide, doit être un UUID.") from exc

    # 1. Vérifier l'idempotence AVANT les règles métier
    existing_job = db.query(BeerCallJob).filter(
        BeerCallJob.creator_id == creator_id,
        BeerCallJob.idempotency_key == idempotency_key
    ).first()

    if existing_job:
        if (existing_job.squad_id != squad_id or 
            existing_job.latitude != latitude or
            existing_job.longitude != longitude or
            existing_job.location_name != location_name):
            raise IdempotencyConflict("Idempotency key déjà utilisée pour une requête différente.")
        return {"status": "processing", "job_id": str(existing_job.id), "job_status": existing_job.status.value}

    # 2. Valider métier (Apéro actif, distance)
    now = datetime.now(timezone.utc)
    user_active_apero = db.query(Apero).filter(
        Apero.creator_id == creator_id,
        Apero.status == AperoStatus.ACTIVE,
        Apero.ended_at > now,
    ).first()

    if user_active_apero:
        raise ValueError("Tu as déjà lancé un Beer Call il y a moins de 4 heures ! Laisse les autres profiter de celui-ci avant d'en recréer un.")

    active_aperos = db.query(Apero).filter(
        Apero.squad_id == squad_id,
        Apero.status == AperoStatus.ACTIVE,
        Apero.ended_at > now,
    ).all()

    for existing_apero in active_aperos:
        distance = calculate_geodistance(
            latitude, longitude,
            existing_apero.latitude, existing_apero.longitude
        )
        if distance <= 500:
            raise ValueError(f"Un Beer Call est déjà en cours tout près ({int(distance)}m) ! Rejoins-le plutôt.")

    # 3. Créer le job en base de données
    job_id = uuid.uuid4()
    new_job = BeerCallJob(
        id=job_id,
        creator_id=creator_id,
        squad_id=squad_id,
        idempotency_key=idempotency_key,
        status=BeerCallJobStatus.UPLOADING,
        latitude=latitude,
        longitude=longitude,
        location_name=location_name
    )

    try:
        db.add(new_job)
        db.commit()
    except IntegrityError:
        # Collision d'idempotence concurrente : un autre thread l'a créé entre temps
        db.rollback()
        existing_job = db.query(BeerCallJob).filter(
            BeerCallJob.creator_id == creator_id,
            BeerCallJob.idempotency_key == idempotency_key
        ).first()
        if existing_job:
            if (existing_job.squad_id != squad_id or 
                existing_job.latitude != latitude or
                existing_job.longitude != longitude or
                existing_job.location_name != location_name):
                raise IdempotencyConflict("Idempotency key déjà utilisée pour une requête différente.")
            return {"status": "processing", "job_id": str(existing_job.id), "job_status": existing_job.status.value}
        raise RuntimeError("Conflit lors de la création du job.")

    # 4. Écrire le fichier
    os.makedirs("uploads/jobs", exist_ok=True)
    tmp_path = f"uploads/jobs/{job_id}.tmp"
    input_path = f"uploads/jobs/{job_id}.input"

    try:
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)
        os.rename(tmp_path, input_path)
    except Exception as e:
        logger.error(f"Erreur d'écriture du fichier pour le job {job_id}: {e}")
        db.delete(new_job)
        db.commit()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise RuntimeError("Erreur système lors de l'upload du fichier.")

    # 5. Marquer pending
    new_job.status = BeerCallJobStatus.PENDING
    db.commit()

    log_job_timing(
        phase="enqueued",
        job_id=new_job.id,
        pid=os.getpid(),
        attempt=new_job.attempts,
        status="pending",
    )

    return {"status": "processing", "job_id": str(new_job.id), "job_status": new_job.status.value}

def process_claimed_job(db_factory, job_id: uuid.UUID, owner_id: str):
    """
    Traite un job récupéré par un worker.
    db_factory est une fonction retournant une nouvelle session DB pour pouvoir faire des requêtes hors de la transaction de lease.
    """
    db = db_factory()
    try:
        # Lire le job pour récupérer les infos
        job = db.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()
        if not job or job.owner_id != owner_id or job.status != BeerCallJobStatus.RUNNING:
            return

        pid = os.getpid()
        attempt = job.attempts

        # Chemins des fichiers
        input_path = f"uploads/jobs/{job.id}.input"
        
        # 1. Lecture fichier : une erreur de lecture est une erreur technique attendue
        # et doit suivre le même chemin de finalisation que l'ancienne sémantique
        # (PENDING avec retries puis FAILED au plafond), jamais laisser le job RUNNING.
        file_bytes: bytes | None = None
        t_file = time.monotonic()
        try:
            with open(input_path, "rb") as f:
                file_bytes = f.read()
            file_dur = (time.monotonic() - t_file) * 1000.0
            log_job_timing("file_read", job_id, pid, attempt, duration_ms=file_dur, status="success")
        except Exception as e:
            file_dur = (time.monotonic() - t_file) * 1000.0
            log_job_timing("file_read", job_id, pid, attempt, duration_ms=file_dur, status="error", error=type(e).__name__)
            logger.exception("Photo read failed for job %s", job_id)
            final_status = BeerCallJobStatus.FAILED
            error_message = "Erreur inattendue lors du traitement."

        # 2. YOLO inference SEULE (les vérifications métier passent sous verrou à la finalisation)
        if file_bytes is not None:
            t_det = time.monotonic()
            try:
                ia_validation = is_drink_detected(file_bytes)
                det_dur = (time.monotonic() - t_det) * 1000.0

                if not ia_validation:
                    log_job_timing("detector_total", job_id, pid, attempt, duration_ms=det_dur, status="rejected")
                    raise ValueError("Pas de boisson, pas de Bar ! -15 Caps 📉")

                log_job_timing("detector_total", job_id, pid, attempt, duration_ms=det_dur, status="success")
                final_status = BeerCallJobStatus.SUCCEEDED
                error_message = None

            except ValueError as e:
                final_status = BeerCallJobStatus.REJECTED
                error_message = str(e)
            except RuntimeError as e:
                det_dur = (time.monotonic() - t_det) * 1000.0
                log_job_timing("detector_total", job_id, pid, attempt, duration_ms=det_dur, status="error", error=type(e).__name__)
                # IA technical error
                final_status = BeerCallJobStatus.PENDING
                error_message = str(e)
            except Exception as e:
                det_dur = (time.monotonic() - t_det) * 1000.0
                log_job_timing("detector_total", job_id, pid, attempt, duration_ms=det_dur, status="error", error=type(e).__name__)
                final_status = BeerCallJobStatus.FAILED
                error_message = "Erreur inattendue lors du traitement."
                logger.exception("Unexpected error in process_claimed_job")

        # 3. Phase de finalisation (verrou final, commit compris)
        t_fin = time.monotonic()
        db_finalize = db_factory()
        try:
            locked_job = db_finalize.query(BeerCallJob).with_for_update().filter(BeerCallJob.id == job_id).first()
            if not locked_job or locked_job.owner_id != owner_id or locked_job.status != BeerCallJobStatus.RUNNING:
                fin_dur = (time.monotonic() - t_fin) * 1000.0
                log_job_timing("finalization", job_id, pid, attempt, duration_ms=fin_dur, status="lease_lost")
                return # Perte du lease
                
            # Verrou sur la Squad pour éviter la création concurrente de deux apéros dans la même squad
            locked_squad = db_finalize.query(Squad).with_for_update().filter(Squad.id == locked_job.squad_id).first()
            
            # Verrou sur l'utilisateur pour éviter la création concurrente dans plusieurs squads
            locked_user = db_finalize.query(User).with_for_update().filter(User.id == locked_job.creator_id).first()
            
            # Vérifications métier sous verrou si le YOLO a réussi
            if final_status == BeerCallJobStatus.SUCCEEDED:
                now = datetime.now(timezone.utc)
                user_active_apero = db_finalize.query(Apero).filter(
                    Apero.creator_id == locked_job.creator_id,
                    Apero.status == AperoStatus.ACTIVE,
                    Apero.ended_at > now,
                ).first()

                if user_active_apero:
                    final_status = BeerCallJobStatus.REJECTED
                    error_message = "Tu as déjà lancé un Beer Call il y a moins de 4 heures ! Laisse les autres profiter de celui-ci avant d'en recréer un."

                active_aperos = db_finalize.query(Apero).filter(
                    Apero.squad_id == locked_job.squad_id,
                    Apero.status == AperoStatus.ACTIVE,
                    Apero.ended_at > now,
                ).all()

                for existing_apero in active_aperos:
                    distance = calculate_geodistance(
                        locked_job.latitude, locked_job.longitude,
                        existing_apero.latitude, existing_apero.longitude
                    )
                    if distance <= 500:
                        final_status = BeerCallJobStatus.REJECTED
                        error_message = f"Un Beer Call est déjà en cours tout près ({int(distance)}m) ! Rejoins-le plutôt."
            
            if final_status == BeerCallJobStatus.SUCCEEDED:
                # Promotion photo
                os.makedirs("uploads/aperos", exist_ok=True)
                apero_photo_path = f"uploads/aperos/{locked_job.id}.jpg" # we assume JPG or fallback, but let's use job.id for uniqueness
                shutil.copy(input_path, apero_photo_path)
                
                now = datetime.now(timezone.utc)
                new_apero = Apero(
                    squad_id=locked_job.squad_id,
                    creator_id=locked_job.creator_id,
                    location_name=locked_job.location_name,
                    latitude=locked_job.latitude,
                    longitude=locked_job.longitude,
                    photo_path=apero_photo_path,
                    status=AperoStatus.ACTIVE,
                    started_at=now,
                    ended_at=now + timedelta(hours=4),
                    source_job_id=locked_job.id
                )
                db_finalize.add(new_apero)
                db_finalize.flush()

                creator_participant = AperoParticipant(
                    apero_id=new_apero.id,
                    user_id=locked_job.creator_id,
                    status=ParticipationStatus.JOINED,
                    photo_path=apero_photo_path
                )
                db_finalize.add(creator_participant)
                
                user = db_finalize.query(User).filter(User.id == locked_job.creator_id).first()
                apply_beer_call_creation_rewards(user, locked_job.squad_id, locked_job.location_name, db_finalize)

                locked_job.status = BeerCallJobStatus.SUCCEEDED
                
                # Outbox event CREATE
                event = RealtimeEvent(
                    squad_id=locked_job.squad_id,
                    payload={"type": "REFRESH_SQUAD", "action": "CREATE",
                             "creator_id": locked_job.creator_id}
                )
                db_finalize.add(event)

            elif final_status == BeerCallJobStatus.REJECTED:
                locked_job.status = BeerCallJobStatus.REJECTED
                user = db_finalize.query(User).filter(User.id == locked_job.creator_id).first()
                if "Pas de boisson" in (error_message or ""):
                    handle_ia_fraud(user, db_finalize)
                    
                # Outbox event REJECTED
                event = RealtimeEvent(
                    squad_id=locked_job.squad_id,
                    payload={"type": "REFRESH_SQUAD", "action": "REJECTED"}
                )
                db_finalize.add(event)

            elif final_status == BeerCallJobStatus.PENDING:
                if locked_job.attempts >= 3:
                    locked_job.status = BeerCallJobStatus.FAILED
                else:
                    locked_job.status = BeerCallJobStatus.PENDING
                    locked_job.owner_id = None
                    
            elif final_status == BeerCallJobStatus.FAILED:
                if locked_job.attempts >= 3:
                    locked_job.status = BeerCallJobStatus.FAILED
                else:
                    locked_job.status = BeerCallJobStatus.PENDING
                    locked_job.owner_id = None

            if locked_job.status in (BeerCallJobStatus.SUCCEEDED, BeerCallJobStatus.REJECTED, BeerCallJobStatus.FAILED):
                job_event = RealtimeEvent(
                    squad_id=locked_job.squad_id,
                    payload={"type": "BEER_CALL_JOB_UPDATED", "job_id": str(locked_job.id)}
                )
                db_finalize.add(job_event)

            db_finalize.commit()
            fin_dur = (time.monotonic() - t_fin) * 1000.0
            log_job_timing("finalization", job_id, pid, attempt, duration_ms=fin_dur, status="success")
            log_job_timing("result", job_id, pid, attempt, outcome=locked_job.status.value, status="success")
            
            # Suppression du fichier input une fois terminé terminal
            if locked_job.status in (BeerCallJobStatus.SUCCEEDED, BeerCallJobStatus.REJECTED, BeerCallJobStatus.FAILED):
                if os.path.exists(input_path):
                    os.remove(input_path)

        except Exception as e:
            fin_dur = (time.monotonic() - t_fin) * 1000.0
            log_job_timing("finalization", job_id, pid, attempt, duration_ms=fin_dur, status="error", error=type(e).__name__)
            logger.exception("Finalization failed")
            db_finalize.rollback()
        finally:
            db_finalize.close()

    finally:
        db.close()
