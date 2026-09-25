import pytest
from fastapi.testclient import TestClient

def get_auth_token(client: TestClient, username="shoptestuser"):
    payload = {
        "username": username,
        "password": "Password123!",
        "avatar": {
            "head": "h_1", "body": "b_1", "legs": "l_1", "feet": "f_1",
            "accessory": None, "animation": "idle", "gender": "male"
        }
    }
    # ignore if already exists
    client.post("/api/auth/signup/", json=payload)
    response = client.post("/api/auth/token/", data={"username": username, "password": "Password123!"})
    return response.json()["access_token"]

def test_get_profile(client: TestClient, db_session):
    token = get_auth_token(client, "profileuser")
    headers = {"Authorization": f"Bearer {token}"}
    
    res = client.get("/api/auth/profile/", headers=headers)
    assert res.status_code == 200
    assert "shop_items" in res.json()

def test_get_public_profile(client: TestClient, db_session):
    # Signup user to get an ID
    payload = {
        "username": "publicuser",
        "password": "Password123!",
        "avatar": {
            "head": "h_1", "body": "b_1", "legs": "l_1", "feet": "f_1",
            "accessory": None, "animation": "idle", "gender": "male"
        }
    }
    signup_res = client.post("/api/auth/signup/", json=payload)
    user_id = signup_res.json()["id"]

    token = get_auth_token(client, "vieweruser")
    headers = {"Authorization": f"Bearer {token}"}
    
    res = client.get(f"/api/auth/profile/u_{user_id}/", headers=headers)
    assert res.status_code == 200
    assert res.json()["username"] == "publicuser"

def test_buy_item(client: TestClient, db_session):
    token = get_auth_token(client, "buyeruser")
    headers = {"Authorization": f"Bearer {token}"}
    
    # We don't know the exact shop items seeded, but usually h_2, b_2, etc. exist.
    # Try buying an item. Usually new users have 100 caps.
    # We will buy a random item that is not h_1/b_1. Let's find one via profile.
    profile = client.get("/api/auth/profile/", headers=headers).json()
    item_to_buy = next((item for item in profile["shop_items"] if not item["is_owned"] and item["price"] <= 100), None)
    
    if item_to_buy:
        res = client.post("/api/auth/buy/", json={"item_id": item_to_buy["id"]}, headers=headers)
        assert res.status_code == 200
        assert "Achat r" in res.json()["message"]
        
        # Test duplicate buy
        res2 = client.post("/api/auth/buy/", json={"item_id": item_to_buy["id"]}, headers=headers)
        assert res2.status_code == 400

        # Equip it
        equip_payload = {
            "head": "h_1", "body": "b_1", "legs": "l_1", "feet": "f_1",
            "accessory": item_to_buy["id"], "animation": "idle", "gender": "male"
        }
        res_equip = client.put("/api/auth/equip/", json=equip_payload, headers=headers)
        assert res_equip.status_code == 200
        assert res_equip.json()["avatar"]["accessory"] == item_to_buy["id"]

def test_equip_unowned_item(client: TestClient, db_session):
    token = get_auth_token(client, "equipuser")
    headers = {"Authorization": f"Bearer {token}"}
    
    equip_payload = {
        "head": "h_1", "body": "b_1", "legs": "l_1", "feet": "f_1",
        "accessory": "fake_unowned_item", "animation": "idle", "gender": "male"
    }
    res_equip = client.put("/api/auth/equip/", json=equip_payload, headers=headers)
    assert res_equip.status_code == 403

def test_connections(client: TestClient, db_session):
    token = get_auth_token(client, "connuser")
    headers = {"Authorization": f"Bearer {token}"}
    
    res = client.get("/api/auth/connections/", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)
