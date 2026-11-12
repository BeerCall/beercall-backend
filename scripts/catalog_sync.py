"""Commande ``catalog:sync`` (dry-run ou application).

Depuis ``beercall-backend`` :

    python -m scripts.catalog_sync --dry-run
    python -m scripts.catalog_sync
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from db.database import SessionLocal
from services.avatar_catalog import CatalogError, build_catalog
from services.avatar_catalog_sync import source_file_exists, sync_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronise la table skins avec les fichiers publiés.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calcule le plan sans modifier la base.",
    )
    parser.add_argument("--manifest", type=Path, default=None, help="Manifeste GLB alternatif.")
    parser.add_argument("--models-dir", type=Path, default=None, help="Dossier static/models alternatif.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    options = {}
    if args.manifest is not None:
        options["manifest_path"] = args.manifest
    if args.models_dir is not None:
        options["models_dir"] = args.models_dir

    try:
        catalog = build_catalog(**options)
    except CatalogError as error:
        logging.error("catalogue indisponible : %s", error)
        return 1

    exists = source_file_exists
    if args.models_dir is not None:
        exists = lambda skin_id: source_file_exists(skin_id, args.models_dir)

    db = SessionLocal()
    try:
        report = sync_catalog(
            db,
            catalog,
            dry_run=args.dry_run,
            source_exists=exists,
        )
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
        print(json.dumps(report.snapshot(), ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
