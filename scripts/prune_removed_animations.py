#!/usr/bin/env python3
"""Simule ou applique le retour à Idle des animations retirées.

Par défaut : SIMULATION, aucune écriture en base. `--apply` est requis pour
modifier `avatar_config.animation`. Le manifeste v2 est la source de vérité ;
Idle doit y être présent, sinon le lot échoue.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from db.database import SessionLocal
from models.user import User
from services.animation_pruning import (
    IDLE,
    apply_removed_animations,
    find_removed_animations,
    valid_animation_names,
)

logger = logging.getLogger("animations.pruning")

DEFAULT_MANIFEST_URL = "http://frontend/animations/manifest.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retrait des animations absentes du manifeste.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--manifest", type=Path, help="Chemin local du manifeste v2.")
    source.add_argument(
        "--manifest-url",
        default=DEFAULT_MANIFEST_URL,
        help="URL du manifeste v2 (défaut : frontend du compose).",
    )
    parser.add_argument(
        "--valid-animations",
        help="Liste de noms séparés par des virgules, pour une simulation sans manifeste.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Écrit réellement Idle en base. Sans ce flag : simulation seule.",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON.")
    return parser.parse_args(argv)


def load_manifest(args: argparse.Namespace) -> dict[str, Any]:
    if args.valid_animations:
        names = [name.strip() for name in args.valid_animations.split(",") if name.strip()]
        return {"clips": [{"name": name} for name in names]}
    if args.manifest:
        return json.loads(args.manifest.read_text(encoding="utf-8"))
    with urlopen(args.manifest_url, timeout=10) as response:  # noqa: S310 - URL interne contrôlée
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        manifest = load_manifest(args)
        valid_names = valid_animation_names(manifest)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logger.error("[animations] manifeste refusé : %s", error)
        return 1

    mode = "APPLY" if args.apply else "SIMULATION"
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.avatar_config.isnot(None)).all()
        removals = find_removed_animations(users, valid_names)
        if args.apply:
            applied = apply_removed_animations(db, removals)
            db.commit()
        else:
            applied = len(removals)
            db.rollback()
            for removal in removals:
                logger.info(
                    "[animations][SIMULATION] user %s : %s -> %s",
                    removal.user_id,
                    removal.animation,
                    IDLE,
                )

        summary = {
            "mode": mode,
            "validAnimations": len(valid_names),
            "usersScanned": len(users),
            "usersToReset": len(removals),
            "usersReset": applied if args.apply else 0,
            "removedAnimations": sorted({removal.animation for removal in removals}),
            "removals": [
                {
                    "userId": removal.user_id,
                    "animation": removal.animation,
                    "replacement": IDLE,
                }
                for removal in removals
            ],
        }
        if args.json:
            print(json.dumps(summary, ensure_ascii=False))
        else:
            print(
                f"[animations][{mode}] {summary['usersScanned']} utilisateurs scannés, "
                f"{summary['usersToReset']} à repasser sur {IDLE}, "
                f"animations retirées : {', '.join(summary['removedAnimations']) or 'aucune'}."
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
