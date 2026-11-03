from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
MODELS_DIR = Path(__file__).resolve().parents[1] / "static" / "models"
MANIFEST_PATH = MODELS_DIR / "glb" / "manifest.json"


def _manifest() -> dict:
    import json

    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_is_served_as_json_with_every_catalog_item() -> None:
    response = client.get("/models/glb/manifest.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert len(payload["items"]) == 162
    assert {item["key"] for item in payload["items"]} == {
        item["key"] for item in _manifest()["items"]
    }


def test_published_glb_is_served_with_gltf_binary_mime_and_exact_bytes() -> None:
    item = _manifest()["items"][0]
    disk_path = MODELS_DIR / item["url"].removeprefix("/models/")

    response = client.get(item["url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "model/gltf-binary"
    assert len(response.content) == item["bytes"] == disk_path.stat().st_size
    assert sha256(response.content).hexdigest() == item["sha256"]


def test_unknown_glb_is_a_404_without_an_immutable_response_contract() -> None:
    response = client.get("/models/glb/v1/pieces/Men/unknown.deadbeef.glb")

    assert response.status_code == 404
