import pytest
from fastapi.testclient import TestClient
from main import app
from models.user import User
from models.squad import Squad
from models.beer_call_job import BeerCallJob, BeerCallJobStatus
import uuid
from db.database import SessionLocal

client = TestClient(app)

def get_auth_headers(user):
    from core.security import create_access_token
    token = create_access_token({"sub": user.username})
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def setup_data(db_session):
    user = User(username="enqueue_user", hashed_password="pw")
    squad = Squad(name="enqueue_squad", invite_code="ENQ")
    squad.members.append(user)
    db_session.add(user)
    db_session.add(squad)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(squad)
    return user, squad

def test_enqueue_success(setup_data, db_session):
    user, squad = setup_data
    
    valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

    headers = get_auth_headers(user)
    headers["Idempotency-Key"] = str(uuid.uuid4())
    
    files = {"file": ("test.png", valid_png, "image/png")}
    data = {
        "latitude": 48.8566,
        "longitude": 2.3522,
        "location_name": "Paris"
    }

    response = client.post(f"/api/squads/{squad.id}/beer-calls/", headers=headers, data=data, files=files)
    
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    assert "job_id" in response.json()

    job_id = uuid.UUID(response.json()["job_id"])
    job = db_session.query(BeerCallJob).filter(BeerCallJob.id == job_id).first()
    assert job is not None
    assert job.status == BeerCallJobStatus.PENDING

def test_enqueue_idempotency_replay(setup_data, db_session):
    user, squad = setup_data
    
    valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

    idem_key = str(uuid.uuid4())
    headers = get_auth_headers(user)
    headers["Idempotency-Key"] = idem_key
    
    files = {"file": ("test.png", valid_png, "image/png")}
    data = {
        "latitude": 48.8566,
        "longitude": 2.3522,
        "location_name": "Paris"
    }

    # First call
    response1 = client.post(f"/api/squads/{squad.id}/beer-calls/", headers=headers, data=data, files=files)
    assert response1.status_code == 202

    # Second call (replay)
    files2 = {"file": ("test.png", valid_png, "image/png")}
    response2 = client.post(f"/api/squads/{squad.id}/beer-calls/", headers=headers, data=data, files=files2)
    assert response2.status_code == 202
    assert response1.json()["job_id"] == response2.json()["job_id"]

def test_enqueue_idempotency_concurrent(setup_data):
    import threading
    user, squad = setup_data
    
    valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

    idem_key = str(uuid.uuid4())
    headers = get_auth_headers(user)
    headers["Idempotency-Key"] = idem_key
    
    data = {
        "latitude": 48.8566,
        "longitude": 2.3522,
        "location_name": "Paris"
    }

    results = []

    def make_call():
        files = {"file": ("test.png", valid_png, "image/png")}
        resp = client.post(f"/api/squads/{squad.id}/beer-calls/", headers=headers, data=data, files=files)
        results.append(resp.json())

    # clear dependency override to allow multiple threads
    app.dependency_overrides.clear()

    t1 = threading.Thread(target=make_call)
    t2 = threading.Thread(target=make_call)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both should succeed and return the exact same job ID
    assert len(results) == 2
    assert results[0]["job_id"] == results[1]["job_id"]
    assert results[0]["status"] == "processing"
    assert results[1]["status"] == "processing"


@pytest.mark.parametrize("field,value", [
    ("latitude", "48.800000001"), ("longitude", "2.300000001"), ("location_name", "Other bar"),
])
def test_divergent_idempotent_request_is_rejected(setup_data, field, value):
    user, squad = setup_data
    headers = {**get_auth_headers(user), "Idempotency-Key": str(uuid.uuid4())}
    data = {"latitude": "48.8", "longitude": "2.3", "location_name": "Original bar"}
    files = {"file": ("test.jpg", b"\xff\xd8\xff image", "image/jpeg")}
    first = client.post(f"/api/squads/{squad.id}/beer-calls/", headers=headers, data=data, files=files)
    assert first.status_code == 202
    divergent = client.post(f"/api/squads/{squad.id}/beer-calls/", headers=headers,
                            data={**data, field: value}, files=files)
    assert divergent.status_code == 409


def test_invalid_idempotency_key_is_validation_error(setup_data):
    user, squad = setup_data
    response = client.post(f"/api/squads/{squad.id}/beer-calls/",
                           headers={**get_auth_headers(user), "Idempotency-Key": "invalid"},
                           data={"latitude": "48.8", "longitude": "2.3", "location_name": "Bar"},
                           files={"file": ("test.jpg", b"\xff\xd8\xff image", "image/jpeg")})
    assert response.status_code == 422
