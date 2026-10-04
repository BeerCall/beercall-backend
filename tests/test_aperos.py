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

@patch("api.v1.squads.is_drink_detected")
@patch("api.v1.squads.send_push_notifications")
def test_create_and_decline_beer_call(mock_push, mock_is_drink, client: TestClient, db_session):
    mock_is_drink.return_value = True

    # User 1 creates squad and beer call
    token1 = get_auth_token(client, "user_creator_decline")
    headers1 = {"Authorization": f"Bearer {token1}"}
    
    payload = {"name": "Beer Call Squad", "icon": "🍺", "color": "#123456"}
    res = client.post("/api/squads/", json=payload, headers=headers1)
    squad_id = res.json()["id"]
    invite_code = res.json()["invite_code"]

    data = {
        "latitude": 48.8566,
        "longitude": 2.3522,
        "location_name": "Paris Bar"
    }
    files = {"file": ("test.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
    
    response = client.post(f"/api/squads/{squad_id}/beer-calls/", data=data, files=files, headers=headers1)
    assert response.status_code == 202
    
    from models.apero import Apero
    apero = db_session.query(Apero).filter(Apero.squad_id == squad_id).first()
    apero_id = apero.id

    # User 2 joins squad and declines beer call
    token2 = get_auth_token(client, "user_decliner")
    headers2 = {"Authorization": f"Bearer {token2}"}
    client.post("/api/squads/join", json={"invite_code": invite_code}, headers=headers2)

    decline_data = {"excuse": "I am tired"}
    response = client.post(f"/api/squads/{squad_id}/beer-calls/bc_{apero_id}/decline/", json=decline_data, headers=headers2)
    assert response.status_code == 200
    assert response.json()["bonus"] == 15

@patch("api.v1.squads.is_drink_detected")
@patch("api.v1.squads.send_push_notifications")
def test_create_and_join_beer_call(mock_push, mock_is_drink, client: TestClient, db_session):
    mock_is_drink.return_value = True

    # User 1 creates squad and beer call
    token1 = get_auth_token(client, "user_creator_join")
    headers1 = {"Authorization": f"Bearer {token1}"}
    
    payload = {"name": "Beer Call Squad 2", "icon": "🍺", "color": "#123456"}
    res = client.post("/api/squads/", json=payload, headers=headers1)
    squad_id = res.json()["id"]
    invite_code = res.json()["invite_code"]

    data = {
        "latitude": 48.8566,
        "longitude": 2.3522,
        "location_name": "Paris Bar"
    }
    files = {"file": ("test.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
    
    response = client.post(f"/api/squads/{squad_id}/beer-calls/", data=data, files=files, headers=headers1)
    assert response.status_code == 202
    
    from models.apero import Apero
    apero = db_session.query(Apero).filter(Apero.squad_id == squad_id).first()
    apero_id = apero.id

    # User 2 joins squad and joins beer call
    token2 = get_auth_token(client, "user_joiner_apero")
    headers2 = {"Authorization": f"Bearer {token2}"}
    client.post("/api/squads/join", json={"invite_code": invite_code}, headers=headers2)

    join_data = {
        "lat": 48.8566,
        "lon": 2.3522
    }
    files = {"file": ("test2.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
    response = client.post(f"/api/squads/{squad_id}/beer-calls/bc_{apero_id}/join/", data=join_data, files=files, headers=headers2)
    assert response.status_code == 200
    assert "bonus" in response.json()
