import pytest
from fastapi.testclient import TestClient

def test_signup_success(client: TestClient, db_session):
    payload = {
        "username": "testuser",
        "password": "Password123!",
        "avatar": {
            "head": "h_1",
            "body": "b_1",
            "legs": "l_1",
            "feet": "f_1",
            "accessory": None, "animation": "idle", "gender": "male"
        }
    }
    response = client.post("/api/auth/signup/", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert "access_token" in data

def test_signup_duplicate_username(client: TestClient, db_session):
    payload = {
        "username": "testuser2",
        "password": "Password123!",
        "avatar": {
            "head": "h_1",
            "body": "b_1",
            "legs": "l_1",
            "feet": "f_1",
            "accessory": None, "animation": "idle", "gender": "male"
        }
    }
    client.post("/api/auth/signup/", json=payload)
    response = client.post("/api/auth/signup/", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "Ce nom d'avatar est déjà pris !"

def test_login_success(client: TestClient, db_session):
    # Setup
    payload = {
        "username": "logintest",
        "password": "Password123!",
        "avatar": {
            "head": "h_1",
            "body": "b_1",
            "legs": "l_1",
            "feet": "f_1",
            "accessory": None, "animation": "idle", "gender": "male"
        }
    }
    client.post("/api/auth/signup/", json=payload)

    # Login
    response = client.post(
        "/api/auth/token/", 
        data={"username": "logintest", "password": "Password123!"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_login_fail(client: TestClient, db_session):
    response = client.post(
        "/api/auth/token/", 
        data={"username": "not_exist", "password": "Password123!"}
    )
    assert response.status_code == 401

def test_get_me(client: TestClient, db_session):
    # Setup
    payload = {
        "username": "metest",
        "password": "Password123!",
        "avatar": {
            "head": "h_1",
            "body": "b_1",
            "legs": "l_1",
            "feet": "f_1",
            "accessory": None, "animation": "idle", "gender": "male"
        }
    }
    signup_res = client.post("/api/auth/signup/", json=payload)
    token = signup_res.json()["access_token"]
    
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["username"] == "metest"
