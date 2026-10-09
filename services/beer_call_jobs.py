import os
import uuid
import logging
import shutil
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
from services.notifications import send_push_notifications

logger = logging.getLogger(__name__)

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
        uuid.UUID(idempotency_key)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Format Idempotency-Key invalide, doit être un UUID.")

    # 1. Vérifier l'idempotence AVANT les règles métier
    existing_job = db.query(BeerCallJob).filter(
        BeerCallJob.creator_id == creator_id,
        BeerCallJob.idempotency_key == idempotency_key
    ).first()

    if existing_job:
        if (existing_job.squad_id != squad_id or 
            abs(existing_job.latitude - latitude) > 0.0001 or 
            abs(existing_job.longitude - longitude) > 0.0001 or
            existing_job.location_name != location_name):
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="Idempotency key déjà utilisée pour une requête différente.")
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
                abs(existing_job.latitude - latitude) > 0.0001 or 
                abs(existing_job.longitude - longitude) > 0.0001 or
                existing_job.location_name != location_name):
                from fastapi import HTTPException
                raise HTTPException(status_code=409, detail="Idempotency key déjà utilisée pour une requête différente.")
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

        # Chemins des fichiers
        input_path = f"uploads/jobs/{job.id}.input"
        
        # 1. YOLO inference SEULE (les vérifications métier passent sous verrou à la finalisation)
        try:
            with open(input_path, "rb") as f:
                file_bytes = f.read()

            ia_validation = is_drink_detected(file_bytes)

            if not ia_validation:
                raise ValueError("Pas de boisson, pas de Bar ! -15 Caps 📉")
                
            final_status = BeerCallJobStatus.SUCCEEDED
            error_message = None

        except ValueError as e:
            final_status = BeerCallJobStatus.REJECTED
            error_message = str(e)
        except RuntimeError as e:
            # IA technical error
            final_status = BeerCallJobStatus.PENDING
            error_message = str(e)
        except Exception as e:
            final_status = BeerCallJobStatus.FAILED
            error_message = "Erreur inattendue lors du traitement."
            logger.exception("Unexpected error in process_claimed_job")

        # 2. Phase de finalisation (verrou final)
        db_finalize = db_factory()
        apero_created = False
        try:
            locked_job = db_finalize.query(BeerCallJob).with_for_update().filter(BeerCallJob.id == job_id).first()
            if not locked_job or locked_job.owner_id != owner_id or locked_job.status != BeerCallJobStatus.RUNNING:
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
                    payload={"type": "REFRESH_SQUAD", "action": "CREATE"}
                )
                db_finalize.add(event)
                apero_created = True

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

            db_finalize.commit()
            
            # Suppression du fichier input une fois terminé terminal
            if locked_job.status in (BeerCallJobStatus.SUCCEEDED, BeerCallJobStatus.REJECTED, BeerCallJobStatus.FAILED):
                if os.path.exists(input_path):
                    os.remove(input_path)

        except Exception as e:
            logger.exception("Finalization failed")
            db_finalize.rollback()
        finally:
            db_finalize.close()

        if apero_created:
            pass

    finally:
        db.close()
