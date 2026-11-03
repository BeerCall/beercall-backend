import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import config
from models.avatar_render_job import AvatarRenderJob
from models.user import User
from schemas.avatar_sprites import AvatarSprites

# Constantes de module, monkeypatchables par les tests
ENABLED: bool = getattr(config, "AVATAR_SPRITES_ENABLED", True)
RENDERER_URL: str = getattr(config, "AVATAR_RENDERER_URL", "http://127.0.0.1:8787")
UPLOADS_DIR: str = getattr(config, "AVATAR_UPLOADS_DIR", "uploads")
RENDERER_VERSION: int = getattr(config, "AVATAR_RENDERER_VERSION", 1)

HEX_16_RE = re.compile(r"^[0-9a-f]{16}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def normalize_avatar_payload(raw_config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Normalise le payload pour le rendu des sprites.
    Contrôle les 4 pièces obligatoires et le genre, sans modifier la config en base.
    """
    if not isinstance(raw_config, dict):
        return None

    # 4 pièces obligatoires (head, body, legs, feet)
    head = raw_config.get("head")
    body = raw_config.get("body")
    legs = raw_config.get("legs")
    feet = raw_config.get("feet")

    for part in (head, body, legs, feet):
        if not part or not isinstance(part, str) or part.lower() == "none":
            return None
        if not SAFE_ID_RE.match(part) or "/" in part or "\\" in part or ".." in part:
            return None

    # Accessoire optionnel : absent, None, "" ou "none" -> "none"
    acc = raw_config.get("accessory")
    if not acc or not isinstance(acc, str) or acc.lower() == "none":
        accessory = "none"
    else:
        if not SAFE_ID_RE.match(acc) or "/" in acc or "\\" in acc or ".." in acc:
            return None
        accessory = acc

    # Déduction ou validation du genre
    gender = raw_config.get("gender")
    if gender and isinstance(gender, str) and gender in ("Men", "Women"):
        normalized_gender = gender
    else:
        # Déduire du préfixe Men_ ou Women_ des pièces
        prefixes = set()
        for piece in (head, body, legs, feet):
            if piece.startswith("Men_"):
                prefixes.add("Men")
            elif piece.startswith("Women_"):
                prefixes.add("Women")
        if len(prefixes) == 1:
            normalized_gender = prefixes.pop()
        else:
            return None

    return {
        "accessory": accessory,
        "body": body,
        "feet": feet,
        "gender": normalized_gender,
        "head": head,
        "legs": legs,
    }


def compute_config_hash(normalized_payload: Dict[str, Any]) -> str:
    """
    Calcule le hash SHA-256 tronqué à 16 caractères hexadécimaux
    sur le JSON canonique trié avec la version du renderer.
    """
    payload_to_hash = dict(normalized_payload)
    payload_to_hash["v"] = RENDERER_VERSION
    canonical_json = json.dumps(
        payload_to_hash,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()[:16]


def get_sprites_dir(config_hash: str) -> Path:
    if not HEX_16_RE.match(config_hash):
        raise ValueError(f"Hash de configuration invalide : '{config_hash}'")
    return Path(UPLOADS_DIR) / "avatars" / "v1" / config_hash


def has_valid_published_sprites(config_hash: str) -> bool:
    """Vérifie la présence et la non-vacuité des trois sprites publiés sur disque."""
    try:
        sprites_dir = get_sprites_dir(config_hash)
    except ValueError:
        return False

    head_file = sprites_dir / "head.webp"
    portrait_file = sprites_dir / "portrait.webp"
    body_file = sprites_dir / "body.webp"

    return (
        head_file.is_file()
        and head_file.stat().st_size > 0
        and portrait_file.is_file()
        and portrait_file.stat().st_size > 0
        and body_file.is_file()
        and body_file.stat().st_size > 0
    )


def sprites_published(config_hash: str) -> bool:
    """Vérifie la présence des trois images et du marqueur de complétude atomique."""
    try:
        sprites_dir = get_sprites_dir(config_hash)
    except ValueError:
        return False

    return (
        (sprites_dir / ".complete").is_file()
        and has_valid_published_sprites(config_hash)
    )


def avatar_sprites_for(user: User) -> AvatarSprites:
    """
    Construit l'état public des sprites à partir des seules colonnes de l'utilisateur.
    N'expose les URLs que si la version courante est publiée.
    """
    status = user.avatar_render_status
    if not status or status == "missing":
        return AvatarSprites(status="missing", pending=False)
    if status == "disabled":
        return AvatarSprites(status="disabled", pending=False)
    if status == "failed":
        return AvatarSprites(status="failed", pending=False)
    if status == "pending":
        return AvatarSprites(status="pending", pending=True)
    if status == "ready":
        # Seule la version courante publiée est exposée
        if (
            user.avatar_config_hash
            and user.avatar_sprite_hash == user.avatar_config_hash
            and HEX_16_RE.match(user.avatar_sprite_hash)
        ):
            h = user.avatar_sprite_hash
            return AvatarSprites(
                hash=h,
                head_url=f"/uploads/avatars/v1/{h}/head.webp",
                portrait_url=f"/uploads/avatars/v1/{h}/portrait.webp",
                body_url=f"/uploads/avatars/v1/{h}/body.webp",
                pending=False,
                status="ready",
            )
        # Si le sprite publié est périmé, ne pas afficher l'ancienne tenue
        return AvatarSprites(status="pending", pending=True)

    return AvatarSprites(status="missing", pending=False)


def schedule_avatar_render(db: Session, user: User) -> AvatarSprites:
    """
    Planifie le rendu des sprites pour un utilisateur.
    N'effectue aucun commit en base et aucun appel HTTP vers le renderer.
    """
    # 1. Flag désactivé
    if not ENABLED:
        user.avatar_render_status = "disabled"
        return AvatarSprites(status="disabled", pending=False)

    # 2. Config incomplète ou invalide
    normalized = normalize_avatar_payload(user.avatar_config)
    if not normalized:
        user.avatar_config_hash = None
        user.avatar_sprite_hash = None
        user.avatar_render_status = "missing"
        return AvatarSprites(status="missing", pending=False)

    # 3. Calcul du hash canonique
    config_hash = compute_config_hash(normalized)
    user.avatar_config_hash = config_hash

    # 4. Cache hit : les fichiers existent déjà sur disque
    if has_valid_published_sprites(config_hash):
        user.avatar_sprite_hash = config_hash
        user.avatar_render_status = "ready"
        return avatar_sprites_for(user)

    # 5. Nécessite un rendu
    user.avatar_render_status = "pending"

    # Vérification d'un job existant
    job = db.query(AvatarRenderJob).filter_by(config_hash=config_hash).first()
    if job:
        if job.status == "failed":
            job.status = "queued"
            job.attempts = 0
            job.last_error = None
            job.run_after = datetime.now(timezone.utc)
            db.flush()
        elif job.status == "done" and not has_valid_published_sprites(config_hash):
            job.status = "queued"
            job.attempts = 0
            job.last_error = None
            job.run_after = datetime.now(timezone.utc)
            db.flush()
    else:
        # Résolution de la concurrence via savepoint sans rollback de la transaction externe
        try:
            with db.begin_nested():
                new_job = AvatarRenderJob(
                    config_hash=config_hash,
                    avatar_config=normalized,
                    status="queued",
                    attempts=0,
                )
                db.add(new_job)
                db.flush()
        except IntegrityError:
            job = db.query(AvatarRenderJob).filter_by(config_hash=config_hash).first()
            if job and job.status == "failed":
                job.status = "queued"
                job.attempts = 0
                job.last_error = None

    return avatar_sprites_for(user)
