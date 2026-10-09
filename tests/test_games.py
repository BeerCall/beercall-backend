import pytest
import uuid
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

def get_auth_token(client: TestClient, username="gametestuser"):
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
def test_games_lifecycle(mock_push, mock_is_drink, client: TestClient, db_session, run_beer_job):
    mock_is_drink.return_value = True

    # User 1
    token1 = get_auth_token(client, "user_game_creator")
    headers1 = {"Authorization": f"Bearer {token1}", "Idempotency-Key": str(uuid.uuid4())}
    
    payload = {"name": "Game Squad", "icon": "🎮", "color": "#123456"}
    res = client.post("/api/squads/", json=payload, headers=headers1)
    squad_id = res.json()["id"]
    invite_code = res.json()["invite_code"]

    data = {"latitude": 48.8566, "longitude": 2.3522, "location_name": "Game Bar"}
    files = {"file": ("test.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
    response = client.post(f"/api/squads/{squad_id}/beer-calls/", data=data, files=files, headers=headers1)
    assert response.status_code == 202
    run_beer_job(response)
    
    from models.apero import Apero
    apero = db_session.query(Apero).filter(Apero.squad_id == squad_id).first()
    apero_id = apero.id

    # User 2
    token2 = get_auth_token(client, "user_game_joiner")
    headers2 = {"Authorization": f"Bearer {token2}"}
    client.post("/api/squads/join", json={"invite_code": invite_code}, headers=headers2)
    
    join_data = {"lat": 48.8566, "lon": 2.3522}
    files = {"file": ("test2.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
    client.post(f"/api/squads/{squad_id}/beer-calls/bc_{apero_id}/join/", data=join_data, files=files, headers=headers2)

    # 1. Start game session
    res_start = client.post(f"/api/aperos/{apero_id}/game/start", headers=headers1)
    assert res_start.status_code == 200
    assert "game_id" in res_start.json()

    # 2. Get game state
    res_state = client.get(f"/api/aperos/{apero_id}/game/state", headers=headers1)
    assert res_state.status_code == 200
    assert "game_id" in res_state.json()

    # 3. Post action
    action_data = {"type": "START_MINIGAME"}
    res_action = client.post(f"/api/aperos/{apero_id}/game/action", json=action_data, headers=headers1)
    assert res_action.status_code == 200

def test_games_lifecycle_insufficient_players(client: TestClient, db_session, run_beer_job):
    token1 = get_auth_token(client, "user_alone")
    headers1 = {"Authorization": f"Bearer {token1}", "Idempotency-Key": str(uuid.uuid4())}
    
    res = client.post("/api/squads/", json={"name": "Alone Squad", "icon": "🥺", "color": "#111"}, headers=headers1)
    squad_id = res.json()["id"]

    data = {"latitude": 48.8566, "longitude": 2.3522, "location_name": "Alone Bar"}
    with patch("api.v1.squads.is_drink_detected") as mock_is_drink, \
         patch("api.v1.squads.send_push_notifications"):
        mock_is_drink.return_value = True
        files = {"file": ("test.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
        response = client.post(f"/api/squads/{squad_id}/beer-calls/", data=data, files=files, headers=headers1)
        assert response.status_code == 202
        run_beer_job(response)
        
        from models.apero import Apero
        apero = db_session.query(Apero).filter(Apero.squad_id == squad_id).first()
        apero_id = apero.id

    res_start = client.post(f"/api/aperos/{apero_id}/game/start", headers=headers1)
    assert res_start.status_code == 400
    assert "2 personnes" in res_start.json()["detail"]
