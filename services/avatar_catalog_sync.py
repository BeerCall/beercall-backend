"""Synchronisation de la table ``skins`` depuis le catalogue fichiers.

Règles de correspondance, dans cet ordre :

1. une ligne dont l'id est exactement le ``sourceId`` du fichier est mise à jour ;
2. sinon, une ligne unique de même clé (genre + partie + nom, sans prix) et
   déjà associée à un fichier (``source`` non nul) est mise à jour ;
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
from services.avatar_catalog import DEFAULT_MODELS_DIR, CatalogItem, CatalogResult

logger = logging.getLogger("avatar_catalog")


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


def source_file_exists(
    skin_id: str,
    models_dir: str | Path = DEFAULT_MODELS_DIR,
) -> bool:
    """Vrai si un fichier source porte encore l'id legacy de la ligne."""

    root = Path(models_dir)
    return (root / f"{skin_id}.fbx").is_file() or (root / f"{skin_id}.glb").is_file()


def _row_key(row: Skin) -> str:
    return f"{row.gender}_{row.category}_{row.name}"


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
            # Une ligne legacy sans source n'est adoptée que si son propre id
            # correspond encore à un fichier présent (migration d'une ligne
            # créée avant l'ajout de `source`). Un vrai orphelin, sans fichier,
            # n'est jamais repris.
            if candidate.source is None and not (
                source_exists and source_exists(candidate.id)
            ):
                continue
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
