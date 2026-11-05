#!/usr/bin/env python3
"""
A11 — Script de backfill pour le rendu des sprites d'avatar (CHAL-08).
Scanne les utilisateurs existants par batchs paginés et planifie les jobs manquants.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Optional

# Ajouter la racine backend au sys.path avant les imports de l'application
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy.orm import Session

from db.database import SessionLocal
from models.avatar_render_job import AvatarRenderJob
from models.user import User
from services import avatar_sprites
from services.avatar_sprites import (
    compute_config_hash,
    has_valid_published_sprites,
    normalize_avatar_payload,
    schedule_avatar_render,
)

logger = logging.getLogger("backfill_avatar_sprites")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_backfill(
    session_factory: Callable[[], Session],
    batch_size: int = 200,
    limit: Optional[int] = None,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> dict:
    """
    Exécute le backfill paginé par ID croissant.
    Ne charge jamais tous les utilisateurs en mémoire.
    """
    summary = {
        "scanned": 0,
        "ready": 0,
        "created": 0,
        "ignored": 0,
        "failed": 0,
    }

    last_id = 0
    total_processed = 0

    while True:
        current_limit = batch_size
        if limit is not None:
            remaining = limit - total_processed
            if remaining <= 0:
                break
            current_limit = min(batch_size, remaining)

        db = session_factory()
        users = []
        try:
            users = (
                db.query(User)
                .filter(User.id > last_id)
                .order_by(User.id.asc())
                .limit(current_limit)
                .all()
            )

            if not users:
                break

            for user in users:
                last_id = user.id
                summary["scanned"] += 1
                total_processed += 1

                # Flag désactivé
                if not avatar_sprites.ENABLED:
                    if not dry_run:
                        user.avatar_render_status = "disabled"
                    summary["ignored"] += 1
                    continue

                # Config vide ou invalide
                norm = normalize_avatar_payload(user.avatar_config, user_id=user.id)
                if not norm:
                    if not dry_run:
                        user.avatar_render_status = "missing"
                        user.avatar_config_hash = None
                    summary["ignored"] += 1
                    continue

                h = compute_config_hash(norm)

                # Si les sprites sont déjà sur disque
                if has_valid_published_sprites(h):
                    if not dry_run:
                        user.avatar_config_hash = h
                        user.avatar_sprite_hash = h
                        user.avatar_render_status = "ready"
                    summary["ready"] += 1
                    continue

                # Si l'utilisateur est déjà en échec et qu'on ne force pas le rejeu
                if user.avatar_render_status == "failed" and not retry_failed:
                    summary["ignored"] += 1
                    continue

                # Si un job actif existe déjà pour cette tenue
                existing_job = db.query(AvatarRenderJob).filter_by(config_hash=h).first()
                if existing_job and existing_job.status in ("queued", "running"):
                    if not dry_run:
                        user.avatar_config_hash = h
                        user.avatar_render_status = "pending"
                    summary["ignored"] += 1
                    continue

                if dry_run:
                    # En mode dry-run, aucune écriture
                    if not existing_job or (existing_job.status == "failed" and retry_failed):
                        summary["created"] += 1
                    else:
                        summary["ignored"] += 1
                else:
                    sprites = schedule_avatar_render(db, user)
                    if retry_failed:
                        # Si le job était en échec, s'assurer qu'il est remis en file
                        job = db.query(AvatarRenderJob).filter_by(config_hash=h).first()
                        if job and job.status == "failed":
                            job.status = "queued"
                            job.attempts = 0
                            job.last_error = None
                            db.flush()

                    if sprites.status == "ready":
                        summary["ready"] += 1
                    elif sprites.status == "pending":
                        summary["created"] += 1
                    else:
                        summary["ignored"] += 1

            if not dry_run:
                db.commit()
            else:
                db.rollback()

        except Exception as exc:
            db.rollback()
            summary["failed"] += len(users)
            logger.error("Erreur dans le batch se terminant à id=%d : %s", last_id, exc)
            raise
        finally:
            db.close()

    logger.info(
        "Fin du backfill (dry_run=%s) : scannés=%d, prêts=%d, créés/enfilés=%d, ignorés=%d, erreurs=%d",
        dry_run,
        summary["scanned"],
        summary["ready"],
        summary["created"],
        summary["ignored"],
        summary["failed"],
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill des jobs de rendu pour les avatars existants")
    parser.add_argument("--dry-run", action="store_true", help="Simule sans écrire en base")
    parser.add_argument("--batch-size", type=int, default=200, help="Taille des batchs de pagination (défaut: 200)")
    parser.add_argument("--limit", type=int, default=None, help="Nombre maximal d'utilisateurs à traiter")
    parser.add_argument("--retry-failed", action="store_true", help="Réenfile les tenues en échec")
    args = parser.parse_args()

    run_backfill(
        SessionLocal,
        batch_size=args.batch_size,
        limit=args.limit,
        dry_run=args.dry_run,
        retry_failed=args.retry_failed,
    )
