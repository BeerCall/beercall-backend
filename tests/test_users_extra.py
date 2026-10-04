import pytest
from fastapi.testclient import TestClient

def get_auth_token(client: TestClient, username="shoptestuser"):
    profile = client.get("/api/auth/profile/").json()
    items = profile.get("shop_items", [])
    def get_first(cat):
        for i in items:
            if i["category"] == cat: return i["id"]
        return "none"
    payload = {
        "username": username,
        "password": "Password123!",
        "avatar": {
            "head": get_first("head"),
            "body": get_first("body"),
            "legs": get_first("legs"),
            "feet": get_first("feet"),
            "accessory": None,
            "animation": "idle",
            "gender": "Men"
        }
    }
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
    profile = client.get("/api/auth/profile/").json()
    items = profile.get("shop_items", [])
    def get_first(cat):
        for i in items:
            if i["category"] == cat: return i["id"]
        return "none"
    payload = {
        "username": "publicuser",
        "password": "Password123!",
        "avatar": {
            "head": get_first("head"),
            "body": get_first("body"),
            "legs": get_first("legs"),
            "feet": get_first("feet"),
            "accessory": None,
            "animation": "idle",
            "gender": "Men"
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
    
    profile = client.get("/api/auth/profile/", headers=headers).json()
    item_to_buy = next((item for item in profile["shop_items"] if not item["is_owned"] and item["price"] <= 100), None)
    
    if item_to_buy:
        res = client.post("/api/auth/buy/", json={"item_id": item_to_buy["id"]}, headers=headers)
        assert res.status_code == 200
        assert "Achat" in res.json()["message"]
        
        res2 = client.post("/api/auth/buy/", json={"item_id": item_to_buy["id"]}, headers=headers)
        assert res2.status_code == 400

        current_avatar = profile["avatar"]
        equip_payload = {
            "head": current_avatar.get("head", "none"),
            "body": current_avatar.get("body", "none"),
            "legs": current_avatar.get("legs", "none"),
            "feet": current_avatar.get("feet", "none"),
            "accessory": item_to_buy["id"],
            "animation": current_avatar.get("animation", "idle"),
            "gender": current_avatar.get("gender", "Men")
        }
        res_equip = client.put("/api/auth/equip/", json=equip_payload, headers=headers)
        assert res_equip.status_code == 200
        assert res_equip.json()["avatar"]["accessory"] == item_to_buy["id"]

def test_equip_unowned_item(client: TestClient, db_session):
    token = get_auth_token(client, "equipuser")
    headers = {"Authorization": f"Bearer {token}"}
    
    profile = client.get("/api/auth/profile/", headers=headers).json()
    current_avatar = profile["avatar"]
    
    equip_payload = {
        "head": current_avatar.get("head", "none"),
        "body": current_avatar.get("body", "none"),
        "legs": current_avatar.get("legs", "none"),
        "feet": current_avatar.get("feet", "none"),
        "accessory": "fake_unowned_item",
        "animation": current_avatar.get("animation", "idle"),
        "gender": current_avatar.get("gender", "Men")
    }
    res_equip = client.put("/api/auth/equip/", json=equip_payload, headers=headers)
    assert res_equip.status_code == 403

def test_connections(client: TestClient, db_session):
    token = get_auth_token(client, "connuser")
    headers = {"Authorization": f"Bearer {token}"}
    
    res = client.get("/api/auth/connections/", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)
