"""Synchronisation de la table ``skins`` depuis le catalogue fichiers.

Règles de correspondance, dans cet ordre :

1. une ligne dont l'id est exactement le ``sourceId`` du fichier est mise à jour ;
2. sinon, une ligne unique de même clé (genre + partie + nom, sans prix) est
   mise à jour, même si son ``source`` est encore nul : c'est le cas des
   renommages legacy (paramètres de transform ajoutés au fichier) et cela
   préserve les achats et équipements existants ;
3. sinon, une nouvelle ligne est créée avec l'id du fichier ;
4. plusieurs lignes de même clé sans id exact : conflit, aucune modification ;
5. toute ligne non réclamée passe à ``available = false`` (jamais supprimée).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from models.gamification import Skin
from models.user import User
from services.avatar_catalog import DEFAULT_MODELS_DIR, CatalogItem, CatalogResult
from services.avatar_sprites import schedule_avatar_render

logger = logging.getLogger("avatar_catalog")


def source_file_exists(skin_id: str, models_dir: str | Path = DEFAULT_MODELS_DIR) -> bool:
    """Compatibility for the catalogue inspection/cleanup CLI (not adoption policy)."""
    root = Path(models_dir)
    return (root / f"{skin_id}.fbx").is_file() or (root / f"{skin_id}.glb").is_file()


@dataclass(frozen=True)
class SyncConflict:
    key: str
    skin_ids: tuple[str, ...]
    sources: tuple[str | None, ...]


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    conflicts: list[SyncConflict] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "created": len(self.created),
            "updated": len(self.updated),
            "hidden": len(self.hidden),
            "conflicts": len(self.conflicts),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "created": self.created,
            "updated": self.updated,
            "hidden": self.hidden,
            "conflicts": [
                {
                    "key": conflict.key,
                    "skin_ids": list(conflict.skin_ids),
                    "sources": list(conflict.sources),
                }
                for conflict in self.conflicts
            ],
        }


def _row_key(row: Skin) -> str:
    return f"{row.gender}_{row.category}_{row.name}"


class CatalogRemovalError(RuntimeError):
    """Réattribution impossible après le retrait d'une pièce du catalogue."""


def _free_replacement(db: Session, removed: Skin) -> Skin | None:
    return (
        db.query(Skin)
        .filter(
            Skin.category == removed.category,
            Skin.gender == removed.gender,
            Skin.price_caps == 0,
            Skin.available.is_(True),
            Skin.id != removed.id,
        )
        .order_by(Skin.id.asc())
        .first()
    )


def _reassign_users_for_removed_skin(db: Session, removed: Skin) -> int:
    """Réattribue la pièce retirée à tous ses porteurs et replanifie leur sprite."""
    wearers: list[tuple[User, list[str]]] = []
    for user in db.query(User).all():
        config = user.avatar_config
        if not isinstance(config, dict):
            continue
        slots = [
            slot
            for slot in ("head", "body", "legs", "feet", "accessory")
            if config.get(slot) == removed.id
        ]
        if slots:
            wearers.append((user, slots))

    if not wearers:
        return 0

    needs_clothing = any(slot != "accessory" for _, slots in wearers for slot in slots)
    replacement: Skin | None = None
    if needs_clothing:
        replacement = _free_replacement(db, removed)
        if replacement is None:
            raise CatalogRemovalError(
                f"Aucune pièce gratuite disponible pour {removed.gender}/{removed.category} "
                f"(pièce retirée {removed.id})"
            )

    reassigned = 0
    for user, slots in wearers:
        config = dict(user.avatar_config)
        for slot in slots:
            new_id = "none" if slot == "accessory" else replacement.id
            config[slot] = new_id
            logger.info(
                "catalog removal reassign user=%s removed=%s slot=%s replacement=%s",
                user.id,
                removed.id,
                slot,
                new_id,
            )
        user.avatar_config = config
        if replacement is not None and not any(skin.id == replacement.id for skin in user.skins):
            user.skins.append(replacement)
        schedule_avatar_render(db, user)
        reassigned += len(slots)
    return reassigned


def _update_row(row: Skin, item: CatalogItem, now: datetime) -> None:
    row.name = item.name
    row.gender = item.gender
    row.category = item.category
    row.price_caps = item.price_caps
    row.source = item.source
    row.available = True
    row.updated_at = now


def sync_catalog(
    db: Session,
    catalog: CatalogResult,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    source_exists: Callable[[str], bool] | None = None,
) -> SyncReport:
    """Applique le catalogue à ``skins`` sans jamais changer les ids existants.

    ``dry_run`` calcule exactement le même plan mais ne modifie pas la session.
    Le commit reste à la charge de l'appelant.
    ``source_exists`` is retained for older CLI callers; legacy adoption is by
    unique catalogue key, never conditional on a renamed file still existing.
    """

    timestamp = now or datetime.now(timezone.utc)
    rows = db.query(Skin).all()
    exact = {row.id: row for row in rows}
    by_key: dict[str, list[Skin]] = {}
    for row in rows:
        by_key.setdefault(_row_key(row), []).append(row)

    report = SyncReport()
    claimed: set[str] = set()
    conflict_ids: set[str] = set()

    for item in catalog.items:
        row = exact.get(item.id)
        if row is not None and row.id not in claimed:
            report.updated.append(row.id)
            claimed.add(row.id)
            if not dry_run:
                _update_row(row, item, timestamp)
            continue

        candidates = [
            candidate
            for candidate in by_key.get(item.key, [])
            if candidate.id not in claimed
        ]

        if len(candidates) > 1:
            sources = tuple(candidate.source for candidate in candidates)
            report.conflicts.append(
                SyncConflict(
                    key=item.key,
                    skin_ids=tuple(candidate.id for candidate in candidates),
                    sources=sources,
                )
            )
            conflict_ids.update(candidate.id for candidate in candidates)
            logger.warning(
                "catalog sync conflict key=%s skins=%s sources=%s",
                item.key,
                [candidate.id for candidate in candidates],
                list(sources),
            )
            continue

        if len(candidates) == 1:
            candidate = candidates[0]
            # Une ligne legacy sans source est adoptée par clé : les renommages
            # de fichiers (transform, prix) ne doivent retirer ni achat ni
            # équipement à un joueur.
            report.updated.append(candidate.id)
            claimed.add(candidate.id)
            if not dry_run:
                _update_row(candidate, item, timestamp)
            continue

        report.created.append(item.id)
        claimed.add(item.id)
        if not dry_run:
            db.add(
                Skin(
                    id=item.id,
                    name=item.name,
                    gender=item.gender,
                    category=item.category,
                    price_caps=item.price_caps,
                    source=item.source,
                    available=True,
                    updated_at=timestamp,
                )
            )

    for row in rows:
        if row.id in claimed or row.id in conflict_ids:
            continue
        if row.available is False:
            continue
        report.hidden.append(row.id)
        if not dry_run:
            logger.info("catalog removal: sourceId=%s", row.id)
            _reassign_users_for_removed_skin(db, row)
            row.available = False
            row.updated_at = timestamp

    if not dry_run:
        db.flush()

    logger.info(
        "catalog sync%s: created=%d updated=%d hidden=%d conflicts=%d",
        " (dry-run)" if dry_run else "",
        len(report.created),
        len(report.updated),
        len(report.hidden),
        len(report.conflicts),
    )
    return report
