import hashlib
import json
import logging
from pathlib import Path

import pytest

from services.avatar_catalog import (
    CatalogError,
    build_catalog,
    humanize,
    load_manifest,
    parse_source_name,
)


def _write_manifest(tmp_path: Path, entries: list[dict]) -> tuple[Path, Path]:
    models_dir = tmp_path / "static" / "models"
    manifest_path = models_dir / "glb" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"version": 1, "pipelineVersion": 1, "items": entries}),
        encoding="utf-8",
    )
    return models_dir, manifest_path


def _entry(
    models_dir: Path,
    source_id: str,
    source: str,
    data: bytes,
    *,
    url: str | None = None,
    sha256: str | None = None,
    size: int | None = None,
    subdir: str = "pieces/Men",
) -> dict:
    relative = f"glb/v1/{subdir}/{source_id}.1234abcd.glb"
    target = models_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {
        "key": f"test/{source_id}",
        "sourceId": source_id,
        "source": source,
        "url": url if url is not None else f"/models/{relative}",
        "sha256": sha256 if sha256 is not None else hashlib.sha256(data).hexdigest(),
        "bytes": size if size is not None else len(data),
    }


def test_parse_source_name_reads_metadata_and_excludes_price_from_key():
    parsed = parse_source_name("Men_head_Casual2_0.fbx")

    assert parsed is not None
    assert parsed.gender == "Men"
    assert parsed.category == "head"
    assert parsed.name == "Casual2"
    assert parsed.price_caps == 0
    assert parsed.key == "Men_head_Casual2"
    assert parsed.display_name == "Tête Casual 2"


def test_parse_source_name_keeps_price_as_metadata():
    beer = parse_source_name("Men_accessory_Beer_500.fbx")
    maclaren = parse_source_name("Women_accessory_McLaren_5000.fbx")
    sudowoodo = parse_source_name("Men_accessory_Sudowoodo_600.fbx")

    assert beer is not None and beer.price_caps == 500
    assert maclaren is not None and maclaren.price_caps == 5000
    assert maclaren.key == "Women_accessory_McLaren"
    assert maclaren.display_name == "Accessoire McLaren"
    assert sudowoodo is not None and sudowoodo.price_caps == 600
    assert sudowoodo.key == "Men_accessory_Sudowoodo"
    assert parse_source_name("Men_accessory_Sudowoodo.fbx").price_caps == 0


def test_parse_source_name_ignores_decors_animations_and_unknown_names():
    assert parse_source_name("Bar.fbx") is None
    assert parse_source_name("Swimming_pool.fbx") is None
    assert parse_source_name("Floaty_Island.fbx") is None
    assert parse_source_name("Animations.fbx") is None
    assert parse_source_name("unclassified.fbx") is None
    assert parse_source_name("Men_head_Casual_0.fbx/../../etc") is None


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("Casual2", "Casual 2"),
        ("AstonMartin", "Aston Martin"),
        ("RacingBulls", "Racing Bulls"),
        ("McLaren", "McLaren"),
        ("SciFi", "Sci Fi"),
        ("Swat", "Swat"),
    ],
)
def test_humanize_without_per_file_list(token: str, expected: str):
    assert humanize(token) == expected


def test_build_catalog_accepts_a_valid_entry(tmp_path: Path):
    models_dir, manifest_path = _write_manifest(tmp_path, [])
    entry = _entry(
        models_dir,
        "Men_head_Casual_0",
        "Men_head_Casual_0.fbx",
        b"valid-glb",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"] = [entry]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = build_catalog(manifest_path=manifest_path, models_dir=models_dir)

    assert result.counts == {"available": 1, "rejected": 0, "duplicates": 0}
    assert result.items[0].id == "Men_head_Casual_0"
    assert result.items[0].price_caps == 0
    assert result.items[0].display_name == "Tête Casual"
    assert result.items[0].asset_url == entry["url"]


def test_build_catalog_rejects_bad_url_hash_size_and_missing_file(tmp_path: Path):
    models_dir, manifest_path = _write_manifest(tmp_path, [])
    entries = [
        _entry(
            models_dir,
            "Men_head_Url_0",
            "Men_head_Url_0.fbx",
            b"url",
            url="/uploads/elsewhere.glb",
        ),
        _entry(
            models_dir,
            "Men_head_Hash_0",
            "Men_head_Hash_0.fbx",
            b"hash",
            sha256="not-a-sha",
        ),
        _entry(
            models_dir,
            "Men_head_Size_0",
            "Men_head_Size_0.fbx",
            b"size",
            size=999,
        ),
        _entry(
            models_dir,
            "Men_head_Missing_0",
            "Men_head_Missing_0.fbx",
            b"missing",
        ),
    ]
    mismatch = _entry(
        models_dir,
        "Men_head_Mismatch_0",
        "Men_head_Mismatch_0.fbx",
        b"mismatch",
    )
    mismatch["sourceId"] = "Men_head_Other_0"
    entries.append(mismatch)
    (models_dir / "glb/v1/pieces/Men/Men_head_Missing_0.1234abcd.glb").unlink()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"] = entries
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = build_catalog(manifest_path=manifest_path, models_dir=models_dir)

    assert result.items == []
    reasons = {item.source: item.reason for item in result.rejected}
    assert "URL non autorisée" in reasons["Men_head_Url_0.fbx"]
    assert reasons["Men_head_Hash_0.fbx"] == "sha256 invalide"
    assert "taille incohérente" in reasons["Men_head_Size_0.fbx"]
    assert "fichier absent" in reasons["Men_head_Missing_0.fbx"]
    assert "sourceId incohérent" in reasons["Men_head_Mismatch_0.fbx"]


def test_build_catalog_rejects_hash_mismatch(tmp_path: Path):
    models_dir, manifest_path = _write_manifest(tmp_path, [])
    entry = _entry(
        models_dir,
        "Men_body_Casual_0",
        "Men_body_Casual_0.fbx",
        b"real-bytes",
        sha256=hashlib.sha256(b"other-bytes").hexdigest(),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"] = [entry]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = build_catalog(manifest_path=manifest_path, models_dir=models_dir)

    assert result.items == []
    assert result.rejected[0].reason == "sha256 incohérent"


def test_duplicate_key_refuses_both_files_without_crashing(tmp_path: Path, caplog):
    models_dir, manifest_path = _write_manifest(tmp_path, [])
    first = _entry(
        models_dir,
        "Men_head_Casual_0",
        "Men_head_Casual_0.fbx",
        b"first",
    )
    second = _entry(
        models_dir,
        "Men_head_Casual_900",
        "Men_head_Casual_900.fbx",
        b"second",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"] = [first, second]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    caplog.set_level(logging.WARNING)
    result = build_catalog(manifest_path=manifest_path, models_dir=models_dir)

    assert result.items == []
    assert result.duplicates == {
        "Men_head_Casual": ["Men_head_Casual_0.fbx", "Men_head_Casual_900.fbx"]
    }
    assert len(result.rejected) == 2
    assert "catalog duplicate key Men_head_Casual refused" in caplog.text


def test_build_catalog_ignores_decors_and_animations(tmp_path: Path):
    models_dir, manifest_path = _write_manifest(tmp_path, [])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"] = [
        {
            "sourceId": "Bar",
            "source": "Bar.fbx",
            "url": "/models/glb/v1/decors/_/Bar.1234abcd.glb",
            "sha256": "a" * 64,
            "bytes": 1,
        },
        {
            "sourceId": "Animations",
            "source": "Animations.fbx",
            "url": "/models/glb/v1/decors/_/Animations.1234abcd.glb",
            "sha256": "b" * 64,
            "bytes": 1,
        },
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = build_catalog(manifest_path=manifest_path, models_dir=models_dir)

    assert result.items == []
    assert result.rejected == []
    assert result.duplicates == {}


def test_load_manifest_rejects_missing_or_invalid_file(tmp_path: Path):
    with pytest.raises(CatalogError):
        load_manifest(tmp_path / "missing.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"version": 2, "items": []}), encoding="utf-8")
    with pytest.raises(CatalogError):
        load_manifest(invalid)
