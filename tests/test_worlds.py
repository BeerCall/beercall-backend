import pytest
import uuid
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

def get_auth_token(client: TestClient, username="squadtestuser"):
    payload = {
        "username": username,
        "password": "Password123!",
        "avatar": {
            "head": "h_1", "body": "b_1", "legs": "l_1", "feet": "f_1",
            "accessory": None, "animation": "idle", "gender": "male"
        }
    }
    client.post("/api/auth/signup/", json=payload)
    response = client.post("/api/auth/token/", data={"username": username, "password": "Password123!"})
    return response.json()["access_token"]

@patch("api.v1.squads.is_drink_detected", new_callable=AsyncMock)
@patch("api.v1.squads.send_push_notifications", new_callable=AsyncMock)
def test_beer_call_worlds(mock_push, mock_is_drink, client: TestClient, db_session, run_beer_job):
    mock_is_drink.return_value = True

    token1 = get_auth_token(client, "world_creator")
    headers1 = {"Authorization": f"Bearer {token1}", "Idempotency-Key": str(uuid.uuid4())}
    
    payload = {"name": "World Squad", "icon": "🌍", "color": "#123"}
    res = client.post("/api/squads/", json=payload, headers=headers1)
    squad_id = res.json()["id"]

    data = {
        "latitude": 48.8566,
        "longitude": 2.3522,
        "location_name": "Paris Bar"
    }
    # Fix Magic Number for file upload
    files = {"file": ("test.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
    response = client.post(f"/api/squads/{squad_id}/beer-calls/", data=data, files=files, headers=headers1)
    assert response.status_code == 202
    run_beer_job(response)
    
    from models.apero import Apero
    apero = db_session.query(Apero).filter(Apero.squad_id == squad_id).first()
    apero_id = apero.id

    # World endpoint
    res_world = client.get(f"/api/squads/{squad_id}/beer-calls/bc_{apero_id}/worlds", headers=headers1)
    assert res_world.status_code == 200
    assert "worlds" in res_world.json()
    assert "bar" in res_world.json()["worlds"]
    assert "piscine" in res_world.json()["worlds"]
