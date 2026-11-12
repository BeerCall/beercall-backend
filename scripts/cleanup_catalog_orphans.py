"""Suppression contrôlée des articles masqués sans fichier.

Usage depuis ``beercall-backend`` :

    python -m scripts.cleanup_catalog_orphans --dry-run --expected-skins 26 --expected-links 154
    python -m scripts.cleanup_catalog_orphans --apply --backup /app/backup.json \
        --expected-skins 26 --expected-links 154

Le script refuse d'écrire si les comptes attendus ne correspondent pas, si un
avatar référence encore un article, ou si un article masqué possède un fichier.
La sauvegarde est écrite avant la transaction ; les liens sont supprimés avant
les lignes ``skins`` et le tout est annulé en cas d'erreur.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.database import SessionLocal
from models.gamification import Skin, user_skins
from models.user import User
from services.avatar_catalog_sync import source_file_exists


def _has_file(skin: Skin) -> bool:
    if source_file_exists(skin.id):
        return True
    if skin.source:
        return source_file_exists(Path(skin.source).stem)
    return False


def _collect(db) -> dict[str, Any]:
    hidden = db.query(Skin).filter(Skin.available.is_(False)).order_by(Skin.id).all()
    without_file = [skin for skin in hidden if not _has_file(skin)]
    with_file = [skin for skin in hidden if _has_file(skin)]
    ids = [skin.id for skin in without_file]

    links = []
    if ids:
        links = (
            db.query(user_skins)
            .filter(user_skins.c.skin_id.in_(ids))
            .order_by(user_skins.c.skin_id, user_skins.c.user_id)
            .all()
        )

    referenced = []
    if ids:
        hidden_ids = set(ids)
        for user in db.query(User).all():
            config = user.avatar_config or {}
            for slot in ("head", "body", "legs", "feet", "accessory"):
                value = config.get(slot)
                if value in hidden_ids:
                    referenced.append(
                        {"user_id": user.id, "username": user.username, "slot": slot, "skin_id": value}
                    )

    return {
        "hidden": hidden,
        "without_file": without_file,
        "with_file": with_file,
        "ids": ids,
        "links": links,
        "referenced": referenced,
    }


def _backup_payload(data: dict[str, Any]) -> dict[str, Any]:
    def iso(value):
        return value.isoformat() if value is not None else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "criteria": "skins.available = false et aucun fichier source",
        "skins": [
            {
                "id": skin.id,
                "name": skin.name,
                "gender": skin.gender,
                "category": skin.category,
                "price_caps": skin.price_caps,
                "available": skin.available,
                "source": skin.source,
                "updated_at": iso(skin.updated_at),
            }
            for skin in data["without_file"]
        ],
        "user_skins": [
            {
                "user_id": link.user_id,
                "skin_id": link.skin_id,
                "unlocked_at": iso(link.unlocked_at),
            }
            for link in data["links"]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Supprime les articles masqués sans fichier.")
    parser.add_argument("--dry-run", action="store_true", help="Affiche le plan sans écrire.")
    parser.add_argument("--apply", action="store_true", help="Applique la suppression.")
    parser.add_argument("--backup", type=Path, help="Fichier JSON de sauvegarde (obligatoire avec --apply).")
    parser.add_argument("--expected-skins", type=int, default=None)
    parser.add_argument("--expected-links", type=int, default=None)
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        parser.error("Choisir --dry-run ou --apply.")
    if args.apply and not args.backup:
        parser.error("--backup est obligatoire avec --apply.")

    db = SessionLocal()
    try:
        data = _collect(db)
        print(json.dumps({
            "hidden_total": len(data["hidden"]),
            "without_file": len(data["without_file"]),
            "with_file": [skin.id for skin in data["with_file"]],
            "ids": data["ids"],
            "links": len(data["links"]),
            "avatar_references": data["referenced"],
        }, ensure_ascii=False, indent=2))

        if data["with_file"]:
            print("Refus : un article masqué possède encore un fichier.")
            return 2
        if data["referenced"]:
            print("Refus : un avatar_config référence un article à supprimer.")
            return 2
        if args.expected_skins is not None and len(data["without_file"]) != args.expected_skins:
            print(f"Refus : {len(data['without_file'])} skins au lieu de {args.expected_skins}.")
            return 2
        if args.expected_links is not None and len(data["links"]) != args.expected_links:
            print(f"Refus : {len(data['links'])} liens au lieu de {args.expected_links}.")
            return 2

        if args.dry_run:
            print(f"Dry-run : {len(data['without_file'])} skins et {len(data['links'])} liens seraient supprimés.")
            return 0

        assert args.backup is not None
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        args.backup.write_text(
            json.dumps(_backup_payload(data), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Sauvegarde écrite : {args.backup}")

        try:
            deleted_links = db.execute(user_skins.delete().where(user_skins.c.skin_id.in_(data["ids"]))).rowcount
            deleted_skins = db.query(Skin).filter(Skin.id.in_(data["ids"])).delete(synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()
            raise

        print(json.dumps({
            "deleted_links": deleted_links,
            "deleted_skins": deleted_skins,
            "remaining_skins": db.query(Skin).count(),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
