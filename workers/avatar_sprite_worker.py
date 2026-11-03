#!/usr/bin/env python3
"""
Worker fiable de rendu des sprites d'avatar (A10 / CHAL-08).
Consomme la file `avatar_render_jobs` de façon idempotente et non bloquante.
"""

import argparse
import base64
import io
import logging
import os
import shutil
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from db.database import SessionLocal
from models.avatar_render_job import AvatarRenderJob
from models.user import User
from services import avatar_sprites

logger = logging.getLogger("avatar_sprite_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

shutdown_flag = False


def _handle_signal(*args) -> None:
    global shutdown_flag
    logger.info("Signal d'arrêt reçu, arrêt gracieux du worker sprites...")
    shutdown_flag = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

EXPECTED_DIMENSIONS = {
    "head": (256, 256),
    "portrait": (512, 512),
    "body": (256, 512),
}


def validate_sprite_image(data: bytes, expected_dims: Tuple[int, int], name: str) -> None:
    """Valide rigoureusement le binaire WebP d'un sprite (taille, en-tête RIFF, format, dimensions, alpha)."""
    if len(data) > 2 * 1024 * 1024:
        raise ValueError(f"Image {name} trop volumineuse : {len(data)} octets (max 2 Mo)")
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError(f"Image {name} invalide : en-tête RIFF/WEBP manquant")

    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Impossible de décoder l'image {name} : {exc}") from exc

    if img.format != "WEBP":
        raise ValueError(f"Format de l'image {name} invalide : {img.format} (attendu WEBP)")
    if img.size != expected_dims:
        raise ValueError(f"Dimensions de l'image {name} invalides : {img.size} (attendu {expected_dims})")
    if img.mode not in ("RGBA", "RGB"):
        raise ValueError(f"Mode de couleur invalide pour {name} : {img.mode}")


def publish_sprites_atomic(
    config_hash: str,
    head_bytes: bytes,
    portrait_bytes: bytes,
    body_bytes: bytes,
) -> Path:
    """
    Écrit les trois images et le marqueur de complétude dans un dossier temporaire sur le même volume,
    puis effectue un renommage atomique vers le dossier final.
    """
    target_dir = avatar_sprites.get_sprites_dir(config_hash)
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if avatar_sprites.sprites_published(config_hash):
        return target_dir

    temp_dir = target_dir.parent / f"{config_hash}.tmp-{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        (temp_dir / "head.webp").write_bytes(head_bytes)
        (temp_dir / "portrait.webp").write_bytes(portrait_bytes)
        (temp_dir / "body.webp").write_bytes(body_bytes)
        (temp_dir / ".complete").write_bytes(b"OK\n")

        if target_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            return target_dir

        try:
            temp_dir.rename(target_dir)
        except OSError:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return target_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def reap_expired_jobs(db: Session, now: datetime) -> int:
    """Récupère les jobs running expirés (> 10 minutes) sans boucle infinie."""
    threshold = now - timedelta(minutes=10)
    expired = (
        db.query(AvatarRenderJob)
        .filter(AvatarRenderJob.status == "running", AvatarRenderJob.updated_at < threshold)
        .all()
    )
    for job in expired:
        job.owner_token = None
        job.updated_at = now
        if job.attempts < 3:
            job.status = "queued"
            job.run_after = now + timedelta(seconds=30)
            logger.warning(
                "Job %s expiré (tentative %d/3) -> remis en file dans 30s", job.config_hash, job.attempts
            )
        else:
            job.status = "failed"
            job.last_error = "Expiré après 3 tentatives sans réponse du renderer (> 10 min)"
            db.query(User).filter(
                User.avatar_config_hash == job.config_hash,
                User.avatar_render_status == "pending",
            ).update({"avatar_render_status": "failed"}, synchronize_session=False)
            logger.error("Job %s expiré définitivement -> marqué failed", job.config_hash)

    if expired:
        db.commit()
    return len(expired)


def claim_next_job(
    db: Session, now: datetime
) -> Optional[Tuple[int, str, dict, str, int]]:
    """Réservation courte d'un job queued avec SKIP LOCKED sous PostgreSQL."""
    reap_expired_jobs(db, now)

    query = db.query(AvatarRenderJob).filter(
        AvatarRenderJob.status == "queued",
        AvatarRenderJob.run_after <= now,
    )
    if db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)

    job = query.order_by(AvatarRenderJob.created_at.asc()).first()
    if not job:
        return None

    owner_token = str(uuid.uuid4())
    job.status = "running"
    job.owner_token = owner_token
    job.attempts += 1
    job.updated_at = now
    db.commit()

    return (job.id, job.config_hash, job.avatar_config, owner_token, job.attempts)


def render_avatar_http(
    avatar_config: dict,
    config_hash: str,
    client: Optional[httpx.Client] = None,
) -> Dict[str, bytes]:
    """Appel HTTP vers le renderer Chromium avec contrôle strict des formats et dimensions."""
    url = f"{avatar_sprites.RENDERER_URL.rstrip('/')}/render"
    payload = {"config": avatar_config, "hash": config_hash}

    own_client = False
    if client is None:
        client = httpx.Client(timeout=90.0)
        own_client = True

    try:
        response = client.post(url, json=payload)
    except httpx.RequestError as exc:
        raise RuntimeError(f"Erreur réseau lors de l'appel au renderer : {exc}") from exc
    finally:
        if own_client:
            client.close()

    if response.status_code == 429:
        raise RuntimeError("Renderer surchargé (HTTP 429)")
    if response.status_code >= 400:
        raise RuntimeError(f"Échec du renderer (HTTP {response.status_code}) : {response.text[:200]}")

    try:
        data = response.json()
    except Exception as exc:
        raise ValueError("Réponse du renderer non JSON") from exc

    for key in ("head", "portrait", "body"):
        if key not in data or not isinstance(data[key], str):
            raise ValueError(f"Champ '{key}' manquant dans la réponse du renderer")

    try:
        head_bytes = base64.b64decode(data["head"], validate=True)
        portrait_bytes = base64.b64decode(data["portrait"], validate=True)
        body_bytes = base64.b64decode(data["body"], validate=True)
    except Exception as exc:
        raise ValueError("Décodage base64 des images échoué") from exc

    validate_sprite_image(head_bytes, EXPECTED_DIMENSIONS["head"], "head")
    validate_sprite_image(portrait_bytes, EXPECTED_DIMENSIONS["portrait"], "portrait")
    validate_sprite_image(body_bytes, EXPECTED_DIMENSIONS["body"], "body")

    return {"head": head_bytes, "portrait": portrait_bytes, "body": body_bytes}


def finalize_job_success(
    db: Session, job_id: int, config_hash: str, owner_token: str
) -> bool:
    """CAS (Compare-And-Swap) : finalise en done si le propriétaire courant est toujours valide."""
    now = datetime.now(timezone.utc)
    affected = (
        db.query(AvatarRenderJob)
        .filter(
            AvatarRenderJob.id == job_id,
            AvatarRenderJob.owner_token == owner_token,
            AvatarRenderJob.status == "running",
        )
        .update(
            {"status": "done", "owner_token": None, "updated_at": now, "last_error": None},
            synchronize_session=False,
        )
    )

    if affected > 0:
        # Met à jour les utilisateurs portant toujours cette tenue
        db.query(User).filter(User.avatar_config_hash == config_hash).update(
            {"avatar_sprite_hash": config_hash, "avatar_render_status": "ready"},
            synchronize_session=False,
        )
        db.commit()
        return True

    db.rollback()
    return False


def finalize_job_failure(
    db: Session,
    job_id: int,
    config_hash: str,
    owner_token: str,
    attempts: int,
    error_msg: str,
) -> None:
    """CAS : réenfile avec backoff ou bascule en failed après 3 échecs."""
    now = datetime.now(timezone.utc)
    job = (
        db.query(AvatarRenderJob)
        .filter(
            AvatarRenderJob.id == job_id,
            AvatarRenderJob.owner_token == owner_token,
            AvatarRenderJob.status == "running",
        )
        .first()
    )
    if not job:
        logger.warning("Propriétaire périmé pour le job %s, échec ignoré", config_hash)
        return

    job.last_error = error_msg[:2000]
    job.owner_token = None
    job.updated_at = now

    if attempts < 3:
        backoff_sec = 30 if attempts == 1 else 60
        job.status = "queued"
        job.run_after = now + timedelta(seconds=backoff_sec)
        logger.warning(
            "Job %s en échec (tentative %d/3) : %s -> rejeu dans %ds",
            config_hash,
            attempts,
            error_msg[:100],
            backoff_sec,
        )
    else:
        job.status = "failed"
        logger.error(
            "Job %s définitivement échoué après %d tentatives : %s",
            config_hash,
            attempts,
            error_msg[:100],
        )
        db.query(User).filter(
            User.avatar_config_hash == config_hash,
            User.avatar_render_status == "pending",
        ).update({"avatar_render_status": "failed"}, synchronize_session=False)

    db.commit()


def process_one_job(
    session_factory: Callable[[], Session],
    renderer_client: Optional[httpx.Client] = None,
) -> bool:
    """Traite au plus un job de façon complète. Retourne True si un travail a été effectué."""
    now = datetime.now(timezone.utc)

    db = session_factory()
    try:
        claimed = claim_next_job(db, now)
    finally:
        db.close()

    if not claimed:
        return False

    job_id, config_hash, avatar_config, owner_token, attempts = claimed
    logger.info("Job claimé : id=%d, hash=%s, tentative=%d/3", job_id, config_hash, attempts)

    # Si les fichiers existent déjà sur disque (ex: rejeu après restart)
    if avatar_sprites.sprites_published(config_hash):
        logger.info("Sprites déjà publiés pour %s, finalisation immédiate", config_hash)
        db = session_factory()
        try:
            finalize_job_success(db, job_id, config_hash, owner_token)
        finally:
            db.close()
        return True

    # Rendu HTTP hors transaction
    try:
        images = render_avatar_http(avatar_config, config_hash, client=renderer_client)
        publish_sprites_atomic(
            config_hash,
            images["head"],
            images["portrait"],
            images["body"],
        )
    except Exception as exc:
        err_str = str(exc)
        logger.error("Erreur de rendu pour %s : %s", config_hash, err_str)
        db = session_factory()
        try:
            finalize_job_failure(db, job_id, config_hash, owner_token, attempts, err_str)
        finally:
            db.close()
        return True

    # Finalisation transactionnelle CAS
    db = session_factory()
    try:
        finalized = finalize_job_success(db, job_id, config_hash, owner_token)
        if finalized:
            logger.info("Job %s complété avec succès et publié", config_hash)
        else:
            logger.warning("Job %s : propriétaire périmé lors de la finalisation", config_hash)
    finally:
        db.close()

    return True


def run_worker_loop(once: bool = False) -> None:
    logger.info("Démarrage de la boucle du worker sprites (once=%s)", once)
    while not shutdown_flag:
        try:
            processed = process_one_job(SessionLocal)
            if once:
                break
            if not processed:
                time.sleep(2.0)
        except Exception as exc:
            logger.error("Exception inattendue dans la boucle du worker : %s", exc, exc_info=True)
            if once:
                break
            time.sleep(2.0)

    logger.info("Worker sprites arrêté.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Worker de rendu des sprites d'avatar")
    parser.add_argument("--once", action="store_true", help="Traite au plus un job et s'arrête")
    args = parser.parse_args()

    run_worker_loop(once=args.once)
