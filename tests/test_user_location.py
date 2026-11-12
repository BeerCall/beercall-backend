from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from main import app
from models.squad import Squad
from models.user import User

client = TestClient(app)


def _auth_headers(username: str) -> dict:
    from core.security import create_access_token
    token = create_access_token({"sub": username})
    return {"Authorization": f"Bearer {token}"}


def test_put_location_unauthorized_returns_401() -> None:
    res = client.put("/api/auth/location/", json={"latitude": 48.85, "longitude": 2.35})
    assert res.status_code == 401


def test_put_location_validation_bounds_and_nan(db_session: Session) -> None:
    user = User(username="loc_val_user", hashed_password="pw")
    db_session.add(user)
    db_session.commit()

    headers = _auth_headers("loc_val_user")

    # Hors bornes latitude
    assert client.put("/api/auth/location/", json={"latitude": 90.001, "longitude": 2.35}, headers=headers).status_code == 422
    assert client.put("/api/auth/location/", json={"latitude": -90.001, "longitude": 2.35}, headers=headers).status_code == 422

    # Hors bornes longitude
    assert client.put("/api/auth/location/", json={"latitude": 48.85, "longitude": 180.001}, headers=headers).status_code == 422
    assert client.put("/api/auth/location/", json={"latitude": 48.85, "longitude": -180.001}, headers=headers).status_code == 422

    # NaN / Inf (sérialisés ou invalides)
    assert client.put("/api/auth/location/", json={"latitude": "NaN", "longitude": 2.35}, headers=headers).status_code == 422
    assert client.put("/api/auth/location/", json={"latitude": 48.85, "longitude": "Infinity"}, headers=headers).status_code == 422


def test_put_location_success_and_2d_throttle(db_session: Session) -> None:
    user = User(username="loc_throttle_user", hashed_password="pw")
    db_session.add(user)
    db_session.commit()

    headers = _auth_headers("loc_throttle_user")

    # 1er envoi : succès 204
    res1 = client.put("/api/auth/location/", json={"latitude": 48.8566, "longitude": 2.3522}, headers=headers)
    assert res1.status_code == 204
    assert res1.content == b""

    db_session.refresh(user)
    assert user.last_latitude == 48.8566
    assert user.last_longitude == 2.3522
    t1 = user.last_location_at
    assert t1 is not None

    # 2ème envoi immédiat (< 30s) et proche (~5 mètres) : no-op throttled
    # 0.00004° de latitude ≈ 4.4 mètres
    res2 = client.put("/api/auth/location/", json={"latitude": 48.85664, "longitude": 2.3522}, headers=headers)
    assert res2.status_code == 204

    db_session.refresh(user)
    # Les coordonnées et la date n'ont pas bougé
    assert user.last_latitude == 48.8566
    assert user.last_location_at == t1

    # 3ème envoi immédiat (< 30s) mais distant (> 25m, ~1.1 km) : mis à jour !
    # 0.01° de latitude ≈ 1110 mètres
    res3 = client.put("/api/auth/location/", json={"latitude": 48.8666, "longitude": 2.3522}, headers=headers)
    assert res3.status_code == 204

    db_session.refresh(user)
    assert user.last_latitude == 48.8666
    assert user.last_location_at >= t1

    # 4ème envoi proche mais après > 30s : mis à jour !
    user.last_location_at = datetime.now(timezone.utc) - timedelta(seconds=35)
    db_session.commit()

    res4 = client.put("/api/auth/location/", json={"latitude": 48.86662, "longitude": 2.3522}, headers=headers)
    assert res4.status_code == 204

    db_session.refresh(user)
    assert user.last_latitude == 48.86662


def test_get_member_locations_authorization_and_isolation(db_session: Session) -> None:
    u1 = User(username="s1_user1", hashed_password="pw", last_latitude=48.85, last_longitude=2.35, last_location_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    u2 = User(username="s1_user2", hashed_password="pw", last_latitude=48.86, last_longitude=2.36, last_location_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    u3_unlocated = User(username="s1_unlocated", hashed_password="pw")  # Pas de position
    u4_s2 = User(username="s2_user", hashed_password="pw", last_latitude=49.0, last_longitude=2.0, last_location_at=datetime.now(timezone.utc))

    squad1 = Squad(name="Squad Loc 1", invite_code="SQLOC111")
    squad2 = Squad(name="Squad Loc 2", invite_code="SQLOC222")

    squad1.members.extend([u1, u2, u3_unlocated])
    squad2.members.append(u4_s2)
    db_session.add_all([u1, u2, u3_unlocated, u4_s2, squad1, squad2])
    db_session.commit()

    # 1. Non authentifié -> 401
    assert client.get(f"/api/squads/{squad1.id}/member-locations").status_code == 401

    # 2. Non membre de squad1 -> 403
    res_403 = client.get(f"/api/squads/{squad1.id}/member-locations", headers=_auth_headers("s2_user"))
    assert res_403.status_code == 403
    assert "Tu ne fais pas partie de cette Squad" in res_403.json()["detail"]

    # 3. Squad inexistante -> 404
    res_404 = client.get("/api/squads/999999/member-locations", headers=_auth_headers("s1_user1"))
    assert res_404.status_code == 404
    assert "Squad introuvable" in res_404.json()["detail"]

    # 4. Membre autorisé de squad1
    res = client.get(f"/api/squads/{squad1.id}/member-locations", headers=_auth_headers("s1_user1"))
    assert res.status_code == 200
    data = res.json()

    # Seuls u1 et u2 sont localisés (u3_unlocated exclu, u4_s2 exclu car squad2)
    assert len(data) == 2
    # Tri date décroissante : u2 (il y a 1 min) avant u1 (il y a 5 min)
    assert data[0]["user_id"] == u2.id
    assert data[0]["username"] == "s1_user2"
    assert data[0]["latitude"] == 48.86
    assert data[0]["longitude"] == 2.36
    assert "avatar_sprites" in data[0]

    assert data[1]["user_id"] == u1.id
    assert data[1]["username"] == "s1_user1"
