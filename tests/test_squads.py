import pytest
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

def test_create_squad(client: TestClient, db_session):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    
    payload = {
        "name": "My Test Squad",
        "icon": "🍻",
        "color": "#ff5500"
    }
    response = client.post("/api/squads/", json=payload, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Test Squad"
    assert "invite_code" in data
    assert "id" in data

def test_get_my_squads(client: TestClient, db_session):
    token = get_auth_token(client, "squadtestuser2")
    headers = {"Authorization": f"Bearer {token}"}
    
    response = client.get("/api/squads/", headers=headers)
    assert response.status_code == 200

    payload = {"name": "Squad 2", "icon": "🍺", "color": "#000000"}
    client.post("/api/squads/", json=payload, headers=headers)

    response = client.get("/api/squads/", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Squad 2"

def test_join_squad(client: TestClient, db_session):
    token1 = get_auth_token(client, "user_creator")
    headers1 = {"Authorization": f"Bearer {token1}"}
    
    payload = {"name": "Joinable Squad", "icon": "🍺", "color": "#000000"}
    res = client.post("/api/squads/", json=payload, headers=headers1)
    invite_code = res.json()["invite_code"]

    token2 = get_auth_token(client, "user_joiner")
    headers2 = {"Authorization": f"Bearer {token2}"}

    join_payload = {"invite_code": invite_code}
    with patch("api.v1.squads.send_push_notifications", new_callable=AsyncMock):
        response = client.post("/api/squads/join", json=join_payload, headers=headers2)
        assert response.status_code == 200
        assert "id" in response.json()

def test_join_squad_invalid_code(client: TestClient, db_session):
    token = get_auth_token(client, "user_invalid_join")
    headers = {"Authorization": f"Bearer {token}"}

    join_payload = {"invite_code": "INVALID1"}
    response = client.post("/api/squads/join", json=join_payload, headers=headers)
    assert response.status_code == 404

def test_get_squad_details(client: TestClient, db_session):
    token = get_auth_token(client, "user_details")
    headers = {"Authorization": f"Bearer {token}"}
    
    payload = {"name": "Details Squad", "icon": "🍺", "color": "#123456"}
    res = client.post("/api/squads/", json=payload, headers=headers)
    squad_id = res.json()["id"]

    response = client.get(f"/api/squads/{squad_id}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Details Squad"
    assert "active_beer_call" in data

@patch("api.v1.squads.is_drink_detected", new_callable=AsyncMock)
@patch("api.v1.squads.send_push_notifications", new_callable=AsyncMock)
def test_create_beer_call(mock_push, mock_is_drink, client: TestClient, db_session):
    mock_is_drink.return_value = True

    token = get_auth_token(client, "user_beercall")
    headers = {"Authorization": f"Bearer {token}"}
    
    payload = {"name": "Beer Call Squad", "icon": "🍺", "color": "#123456"}
    res = client.post("/api/squads/", json=payload, headers=headers)
    squad_id = res.json()["id"]

    data = {
        "latitude": 48.8566,
        "longitude": 2.3522,
        "location_name": "Paris Bar"
    }
    files = {"file": ("test.jpg", b"fake_image_data", "image/jpeg")}
    
    response = client.post(f"/api/squads/{squad_id}/beer-calls/", data=data, files=files, headers=headers)
    assert response.status_code == 200
    assert "apero_id" in response.json()
