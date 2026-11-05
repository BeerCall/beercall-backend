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
NUMBER_RE = re.compile(r"^-?\d+(?:d\d+)?$")
POSITION_RE = re.compile(r"^p([xyz])(-?\d+(?:d\d+)?)$")
ROTATION_RE = re.compile(r"^r([xyz])(-?\d+(?:d\d+)?)$")
SCALE_RE = re.compile(r"^s(-?\d+(?:d\d+)?)$")
BONE_RE = re.compile(r"^b([A-Za-z][A-Za-z0-9]*)$")

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


class SourceNameError(ValueError):
    """Nom d'avatar invalide (prix, jeton ou doublon)."""


@dataclass(frozen=True)
class AccessoryTransform:
    """Transform d'accessoire lu dans le nom, rotations en degrés."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: float = 1.0
    bone: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": list(self.position),
            "rotation": list(self.rotation),
            "scale": self.scale,
            "bone": self.bone,
        }


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
    transform: AccessoryTransform | None = None


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
    transform: AccessoryTransform | None = None


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


def display_name_for(name: str, category: str) -> str:
    """Nom lisible pour la boutique, sans liste écrite par fichier."""

    label = CATEGORY_LABELS.get(category, category.title())
    return f"{label} {humanize(name)}".strip()


def _parse_number(token: str) -> float:
    if not NUMBER_RE.fullmatch(token):
        raise SourceNameError(f"nombre invalide : {token}")
    return float(token.replace("d", "."))


def _parse_transform(tokens: list[str]) -> AccessoryTransform:
    position = [0.0, 0.0, 0.0]
    rotation = [0.0, 0.0, 0.0]
    scale = 1.0
    bone: str | None = None
    seen: set[str] = set()
    axis = {"x": 0, "y": 1, "z": 2}

    for token in tokens:
        match = POSITION_RE.fullmatch(token)
        if match:
            if f"p{match.group(1)}" in seen:
                raise SourceNameError(f"jeton en double : {token}")
            seen.add(f"p{match.group(1)}")
            position[axis[match.group(1)]] = _parse_number(match.group(2))
            continue

        match = ROTATION_RE.fullmatch(token)
        if match:
            if f"r{match.group(1)}" in seen:
                raise SourceNameError(f"jeton en double : {token}")
            seen.add(f"r{match.group(1)}")
            rotation[axis[match.group(1)]] = _parse_number(match.group(2))
            continue

        match = SCALE_RE.fullmatch(token)
        if match:
            if "s" in seen:
                raise SourceNameError(f"jeton en double : {token}")
            seen.add("s")
            scale = _parse_number(match.group(1))
            continue

        match = BONE_RE.fullmatch(token)
        if match:
            if "b" in seen:
                raise SourceNameError(f"jeton en double : {token}")
            seen.add("b")
            bone = match.group(1)
            continue

        raise SourceNameError(f"jeton invalide : {token}")

    return AccessoryTransform(
        position=(position[0], position[1], position[2]),
        rotation=(rotation[0], rotation[1], rotation[2]),
        scale=scale,
        bone=bone,
    )


def parse_source_name(source: str) -> ParsedSource | None:
    """Analyse ``Genre_partie_Nom_[jetons]_Prix.fbx``.

    Retourne ``None`` pour les décors, ``Animations`` et les noms non
    classables. Lève ``SourceNameError`` pour un prix ou un jeton invalide.
    """

    if not source or not isinstance(source, str):
        return None
    filename = Path(source).name
    if filename != source or not filename:
        return None
    stem = filename[:-4] if filename.lower().endswith(".fbx") else filename
    if stem in EXCLUDED_SOURCE_IDS:
        return None

    parts = stem.split("_")
    if len(parts) < 3:
        return None
    gender, category, name = parts[0], parts[1], parts[2]
    if gender not in {"Men", "Women"} or category not in CATEGORY_LABELS or not name:
        return None
    if len(parts) < 4:
        raise SourceNameError("prix final manquant")
    price_token = parts[-1]
    if not price_token.isdigit():
        raise SourceNameError(f"prix final invalide : {price_token}")

    tokens = parts[3:-1]
    if category != "accessory" and tokens:
        raise SourceNameError("les jetons sont réservés aux accessoires")
    transform = _parse_transform(tokens) if tokens else None

    return ParsedSource(
        source_id=stem,
        source=filename,
        gender=gender,
        category=category,
        name=name,
        key=f"{gender}_{category}_{name}",
        price_caps=int(price_token),
        display_name=display_name_for(name, category),
        transform=transform,
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
            transform=parsed.transform,
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
        try:
            parsed = parse_source_name(candidate)
        except SourceNameError as error:
            logger.warning("catalog rejected %s: %s", candidate, error)
            result.rejected.append(RejectedItem(source=candidate, reason=str(error)))
            continue
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
