"""Catalogue d'avatars dérivé des fichiers publiés.

La source de vérité est ``static/models/glb/manifest.json`` : chaque entrée
décrit un GLB déjà publié, avec son URL, sa taille et son SHA-256. Ce module ne
touche ni à la base ni au système de fichiers : il produit une vue validée que
la synchronisation (étape suivante) utilisera.

Une entrée est identifiée par ``genre + partie + nom``, sans le prix. Le prix
reste une métadonnée lue dans le suffixe du nom de fichier. Deux fichiers qui
partagent la même clé sont refusés ensemble et journalisés, sans exception.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("avatar_catalog")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS_DIR = BACKEND_ROOT / "static" / "models"
DEFAULT_MANIFEST_PATH = DEFAULT_MODELS_DIR / "glb" / "manifest.json"

ALLOWED_URL_PREFIX = "/models/glb/v1/"
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_RE = re.compile(
    r"^(?P<gender>Men|Women)_(?P<category>head|body|legs|feet|accessory)_"
    r"(?P<name>.+?)(?:_(?P<price>\d+))?$"
)

EXCLUDED_SOURCE_IDS = frozenset(
    {"Bar", "Swimming_pool", "Floaty_Island", "Animations"}
)

CATEGORY_LABELS = {
    "head": "Tête",
    "body": "Corps",
    "legs": "Jambes",
    "feet": "Pieds",
    "accessory": "Accessoire",
}


class CatalogError(ValueError):
    """Manifeste absent ou structurellement invalide."""


@dataclass(frozen=True)
class ParsedSource:
    """Métadonnées lues dans le nom d'un fichier source."""

    source_id: str
    source: str
    gender: str
    category: str
    name: str
    key: str
    price_caps: int
    display_name: str


@dataclass(frozen=True)
class CatalogItem:
    """Article validé, prêt pour la synchronisation."""

    id: str
    source: str
    gender: str
    category: str
    name: str
    key: str
    price_caps: int
    display_name: str
    asset_url: str
    sha256: str
    bytes: int
    available: bool = True


@dataclass(frozen=True)
class RejectedItem:
    """Entrée du manifeste refusée, avec une raison lisible."""

    source: str
    reason: str


@dataclass
class CatalogResult:
    """Résultat d'un scan : articles acceptés, refusés et clés dupliquées."""

    items: list[CatalogItem] = field(default_factory=list)
    rejected: list[RejectedItem] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "available": len(self.items),
            "rejected": len(self.rejected),
            "duplicates": len(self.duplicates),
        }


def humanize(name_token: str) -> str:
    """Transforme un jeton de fichier en nom lisible, sans liste par fichier.

    ``Casual2`` -> ``Casual 2``, ``AstonMartin`` -> ``Aston Martin``,
    ``RacingBulls`` -> ``Racing Bulls``, ``McLaren`` reste ``McLaren``.
    """

    value = re.sub(r"[_\s]+", " ", name_token.strip())
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", value)
    value = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", value)
    value = " ".join(value.split())
    # « McLaren » ne doit pas devenir « Mc Laren ».
    value = re.sub(r"^Mc (?=[A-Z])", "Mc", value)
    return value


def parse_source_name(source: str) -> ParsedSource | None:
    """Analyse ``Men_head_Casual2_0.fbx`` sans le prix dans la clé.

    Retourne ``None`` pour les décors, ``Animations`` et les noms non
    classables. Le prix absent vaut ``0`` (article gratuit).
    """

    if not source or not isinstance(source, str):
        return None
    filename = Path(source).name
    if filename != source or not filename:
        return None
    stem = filename[:-4] if filename.lower().endswith(".fbx") else filename
    if stem in EXCLUDED_SOURCE_IDS:
        return None

    match = SOURCE_RE.fullmatch(stem)
    if not match:
        return None

    gender = match.group("gender")
    category = match.group("category")
    name = match.group("name")
    price = match.group("price")
    if not name:
        return None

    key = f"{gender}_{category}_{name}"
    label = CATEGORY_LABELS.get(category, category.title())
    return ParsedSource(
        source_id=stem,
        source=filename,
        gender=gender,
        category=category,
        name=name,
        key=key,
        price_caps=int(price) if price is not None else 0,
        display_name=f"{label} {humanize(name)}".strip(),
    )


def load_manifest(manifest_path: str | Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    """Charge et valide la structure minimale du manifeste GLB."""

    path = Path(manifest_path)
    if not path.is_file():
        raise CatalogError(f"Manifeste GLB introuvable : {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"Manifeste GLB illisible : {path} ({error})") from error

    if not isinstance(data, dict) or data.get("version") != 1:
        raise CatalogError(f"Manifeste GLB invalide : version 1 attendue ({path})")
    if not isinstance(data.get("items"), list):
        raise CatalogError(f"Manifeste GLB invalide : tableau 'items' attendu ({path})")
    return data


def _safe_model_path(url: str, models_dir: Path) -> Path | None:
    if not url.startswith(ALLOWED_URL_PREFIX):
        return None
    relative = url[len("/models/") :]
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        return None
    candidate = (models_dir / relative).resolve()
    if not candidate.is_relative_to(models_dir.resolve()):
        return None
    return candidate


def _validate_entry(
    entry: dict[str, Any],
    parsed: ParsedSource,
    models_dir: Path,
    verify_hash: bool,
) -> tuple[CatalogItem | None, str | None]:
    source_id = entry.get("sourceId")
    if not isinstance(source_id, str) or not source_id:
        return None, "sourceId manquant"
    if source_id != parsed.source_id:
        return None, f"sourceId incohérent ({source_id} != {parsed.source_id})"

    source = entry.get("source")
    if not isinstance(source, str) or not source:
        return None, "source manquante"
    if Path(source).name != source:
        return None, "source contient un chemin"

    url = entry.get("url")
    if not isinstance(url, str):
        return None, "URL manquante"
    file_path = _safe_model_path(url, models_dir)
    if file_path is None:
        return None, f"URL non autorisée : {url}"

    digest = entry.get("sha256")
    if not isinstance(digest, str) or not HEX_64_RE.fullmatch(digest):
        return None, "sha256 invalide"

    size = entry.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None, "taille invalide"
    if not file_path.is_file():
        return None, f"fichier absent : {file_path.name}"
    actual_size = file_path.stat().st_size
    if actual_size != size:
        return None, f"taille incohérente ({actual_size} != {size})"
    if verify_hash:
        actual_digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual_digest != digest:
            return None, "sha256 incohérent"

    return (
        CatalogItem(
            id=source_id,
            source=source,
            gender=parsed.gender,
            category=parsed.category,
            name=parsed.name,
            key=parsed.key,
            price_caps=parsed.price_caps,
            display_name=parsed.display_name,
            asset_url=url,
            sha256=digest,
            bytes=size,
        ),
        None,
    )


def build_catalog(
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    models_dir: str | Path = DEFAULT_MODELS_DIR,
    verify_hash: bool = True,
) -> CatalogResult:
    """Construit la vue catalogue à partir du manifeste publié.

    Les décors et ``Animations`` sont ignorés. Les autres entrées sont
    validées (URL, hash, taille, fichier). Les clés dupliquées sont refusées
    ensemble et journalisées ; aucune exception n'est levée pour ce cas.
    """

    models_root = Path(models_dir).resolve()
    manifest = load_manifest(manifest_path)
    result = CatalogResult()
    parsed_entries: list[tuple[dict[str, Any], ParsedSource]] = []

    for entry in manifest["items"]:
        if not isinstance(entry, dict):
            result.rejected.append(RejectedItem(source="<non-objet>", reason="entrée invalide"))
            continue
        source_id = entry.get("sourceId")
        source = entry.get("source")
        candidate = source if isinstance(source, str) and source else (
            f"{source_id}.fbx" if isinstance(source_id, str) else ""
        )
        if not candidate:
            result.rejected.append(RejectedItem(source="<sans source>", reason="sourceId/source manquant"))
            continue
        stem = Path(candidate).stem
        if stem in EXCLUDED_SOURCE_IDS:
            continue
        parsed = parse_source_name(candidate)
        if parsed is None:
            result.rejected.append(
                RejectedItem(source=candidate, reason="nom non classable")
            )
            continue
        parsed_entries.append((entry, parsed))

    by_key: dict[str, list[tuple[dict[str, Any], ParsedSource]]] = {}
    for entry, parsed in parsed_entries:
        by_key.setdefault(parsed.key, []).append((entry, parsed))

    for key, group in by_key.items():
        if len(group) > 1:
            sources = sorted(parsed.source for _, parsed in group)
            result.duplicates[key] = sources
            logger.warning("catalog duplicate key %s refused: %s", key, ", ".join(sources))
            for entry, parsed in group:
                result.rejected.append(
                    RejectedItem(
                        source=parsed.source,
                        reason=f"clé dupliquée {key} ({', '.join(sources)})",
                    )
                )
            continue

        entry, parsed = group[0]
        item, reason = _validate_entry(entry, parsed, models_root, verify_hash)
        if item is None:
            reason = reason or "erreur inconnue"
            logger.warning("catalog rejected %s: %s", parsed.source, reason)
            result.rejected.append(RejectedItem(source=parsed.source, reason=reason))
        else:
            result.items.append(item)

    result.items.sort(key=lambda item: item.key)
    logger.info(
        "catalog scan: %d available, %d rejected, %d duplicate keys",
        len(result.items),
        len(result.rejected),
        len(result.duplicates),
    )
    return result
