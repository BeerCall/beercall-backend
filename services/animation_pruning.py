"""Retrait des animations supprimées du catalogue.

Le manifeste est la source de vérité : une animation absente du manifeste ne
doit plus rester équipée. La simulation est le mode par défaut ; `--apply` est
obligatoire pour écrire en base.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from models.user import User

logger = logging.getLogger("animations.pruning")

IDLE = "Idle"


@dataclass(frozen=True)
class AnimationRemoval:
    user_id: int
    animation: str


def valid_animation_names(manifest: dict[str, Any]) -> set[str]:
    """Noms autorisés par le manifeste v2 ; Idle est obligatoire."""
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not clips:
        raise ValueError("Manifeste invalide : clips manquants.")
    names: set[str] = set()
    for clip in clips:
        if not isinstance(clip, dict) or not isinstance(clip.get("name"), str) or not clip["name"]:
            raise ValueError("Manifeste invalide : nom de clip manquant.")
        names.add(clip["name"])
    if IDLE not in names:
        raise ValueError("Le manifeste ne contient pas Idle : retrait refusé.")
    return names


def find_removed_animations(
    users: Iterable[User],
    valid_names: set[str],
) -> list[AnimationRemoval]:
    """Liste les utilisateurs dont l'animation n'existe plus dans le manifeste."""
    removals: list[AnimationRemoval] = []
    for user in users:
        config = user.avatar_config
        if not isinstance(config, dict):
            continue
        animation = config.get("animation")
        if not isinstance(animation, str) or not animation:
            continue
        if animation in valid_names:
            continue
        removals.append(AnimationRemoval(user_id=user.id, animation=animation))
    return sorted(removals, key=lambda item: (item.user_id, item.animation))


def apply_removed_animations(db: Session, removals: Sequence[AnimationRemoval]) -> int:
    """Remplace les animations retirées par Idle. Ne commit pas."""
    applied = 0
    for removal in removals:
        user = db.get(User, removal.user_id)
        if user is None:
            continue
        config = dict(user.avatar_config or {})
        config["animation"] = IDLE
        user.avatar_config = config
        logger.info(
            "[animations] user %s : %s -> %s",
            removal.user_id,
            removal.animation,
            IDLE,
        )
        applied += 1
    return applied
