import os
import uuid
import logging
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from models.beer_call_job import BeerCallJob, BeerCallJobStatus
from models.apero import Apero, AperoStatus
from datetime import datetime, timezone, timedelta
from services.photo_validation import calculate_geodistance

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
    # 1. Vérifier l'idempotence AVANT les règles métier
    existing_job = db.query(BeerCallJob).filter(
        BeerCallJob.creator_id == creator_id,
        BeerCallJob.idempotency_key == idempotency_key
    ).first()

    if existing_job:
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
