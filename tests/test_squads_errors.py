import pytest
import uuid
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

def test_apero_active_within_4_hours(client: TestClient, db_session, run_beer_job):
    token = get_auth_token(client, "user_active_test")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
    
    payload = {"name": "Active Squad", "icon": "🍺", "color": "#123"}
    res = client.post("/api/squads/", json=payload, headers=headers)
    squad_id = res.json()["id"]

    data = {"latitude": 48.8, "longitude": 2.3, "location_name": "Loc"}
    files = {"file": ("test.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
    
    with patch("api.v1.squads.is_drink_detected") as mock_is_drink, \
         patch("api.v1.squads.send_push_notifications", ):
        mock_is_drink.return_value = True
        
        # 1st apero
        res1 = client.post(f"/api/squads/{squad_id}/beer-calls/", data=data, files=files, headers=headers)
        assert res1.status_code == 202
        run_beer_job(res1)
        
        # 2nd apero immediately -> should fail with 400 (already active apero by user)
        files = {"file": ("test2.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
        headers["Idempotency-Key"] = str(uuid.uuid4())
        res2 = client.post(f"/api/squads/{squad_id}/beer-calls/", data=data, files=files, headers=headers)
        assert res2.status_code == 400
        assert "4 heures" in res2.json()["detail"]

@patch("api.v1.squads.is_drink_detected")
def test_apero_ia_fraud(mock_is_drink, client: TestClient, db_session, run_beer_job):
    mock_is_drink.return_value = False # Force YOLO to fail

    token = get_auth_token(client, "user_fraud_test")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
    
    payload = {"name": "Fraud Squad", "icon": "🍺", "color": "#123"}
    res = client.post("/api/squads/", json=payload, headers=headers)
    squad_id = res.json()["id"]

    data = {"latitude": 48.8, "longitude": 2.3, "location_name": "Loc"}
    files = {"file": ("test.jpg", b"\xff\xd8\xff_fake_image_data", "image/jpeg")}
    
    res = client.post(f"/api/squads/{squad_id}/beer-calls/", data=data, files=files, headers=headers)
    assert res.status_code == 202
    run_beer_job(res, drink=False)
    
    from models.user import User
    user = db_session.query(User).filter(User.username == "user_fraud_test").first()
    assert user.ia_fraud_count > 0
