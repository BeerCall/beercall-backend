import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone, timedelta

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

@patch("api.v1.squads.send_push_notifications", new_callable=AsyncMock)
def test_scheduled_beer_call(mock_push, client: TestClient, db_session):
    token1 = get_auth_token(client, "user_sched")
    headers1 = {"Authorization": f"Bearer {token1}"}
    
    payload = {"name": "Sched Squad", "icon": "🍺", "color": "#123456"}
    res = client.post("/api/squads/", json=payload, headers=headers1)
    squad_id = res.json()["id"]

    sched_time = datetime.now(timezone.utc) + timedelta(hours=2)
    data = {
        "latitude": 48.8566,
        "longitude": 2.3522,
        "location_name": "Paris Bar Sched",
        "scheduled_for": sched_time.isoformat()
    }
    
    response = client.post(f"/api/squads/{squad_id}/scheduled-beer-calls/", json=data, headers=headers1)
    assert response.status_code == 200
    apero_id = response.json()["id"]

    # test get details to see if it shows up in scheduled
    res_details = client.get(f"/api/squads/{squad_id}", headers=headers1)
    assert len(res_details.json()["scheduled_beer_calls"]) == 1

    # Start scheduled beer call
    start_data = {
        "latitude": 48.8566,
        "longitude": 2.3522,
    }
    files = {"file": ("test.jpg", b"fake_image_data", "image/jpeg")}
    
    with patch("api.v1.squads.is_drink_detected", new_callable=AsyncMock) as mock_is_drink:
        mock_is_drink.return_value = True
        res_start = client.post(f"/api/squads/{squad_id}/beer-calls/bc_{apero_id}/start/", data=start_data, files=files, headers=headers1)
        assert res_start.status_code == 200
    assert res_start.json()["message"] == "Apéro démarré"


def test_schedule_api_errors_and_post_commit_effects(client: TestClient, db_session) -> None:
    token = get_auth_token(client, "schedule_contract")
    headers = {"Authorization": f"Bearer {token}"}
    squad = client.post("/api/squads/", json={"name": "Schedule", "icon": "🍺", "color": "#123456"}, headers=headers).json()
    squad_id = squad["id"]
    scheduled_for = datetime.now(timezone.utc) + timedelta(hours=2)
    data = {"latitude": 48.8, "longitude": 2.3, "location_name": "Bar", "scheduled_for": scheduled_for.isoformat()}
    from models.apero import Apero, AperoStatus

    def assert_committed(*args, **kwargs) -> None:
        assert db_session.query(Apero).filter(Apero.squad_id == squad_id, Apero.status == AperoStatus.SCHEDULED).count() == 1

    with patch("api.v1.squads.manager.broadcast_to_squad", side_effect=assert_committed) as broadcast, patch("api.v1.squads.notify_scheduled_apero", side_effect=assert_committed) as notify:
        response = client.post(f"/api/squads/{squad_id}/scheduled-beer-calls/", json=data, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "scheduled"
        assert response.json()["location_name"] == "Bar"
        broadcast.assert_called_once_with(squad_id, {"type": "REFRESH_SQUAD", "action": "SCHEDULE"})
        notify.assert_called_once_with([], squad_id, response.json()["id"], "Bar", scheduled_for.isoformat())
        conflict = client.post(f"/api/squads/{squad_id}/scheduled-beer-calls/", json=data, headers=headers)
        assert conflict.status_code == 400
        assert conflict.json() == {"detail": "Un apéro est déjà programmé à cet endroit dans ce créneau horaire"}
        broadcast.assert_called_once()
        notify.assert_called_once()
    past = {**data, "scheduled_for": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    response = client.post(f"/api/squads/{squad_id}/scheduled-beer-calls/", json=past, headers=headers)
    assert response.status_code == 400
    assert response.json() == {"detail": "La date doit être dans le futur"}
    response = client.post(f"/api/squads/{squad_id + 1}/scheduled-beer-calls/", json=data, headers=headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Squad introuvable"}
    other_token = get_auth_token(client, "schedule_outsider")
    response = client.post(f"/api/squads/{squad_id}/scheduled-beer-calls/", json=data, headers={"Authorization": f"Bearer {other_token}"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Tu ne fais pas partie de cette Squad"}
