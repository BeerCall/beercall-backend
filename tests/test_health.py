from fastapi.testclient import TestClient
from main import app
from db.database import get_db

client = TestClient(app)

def test_liveness():
    response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_readiness_ok():
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}

def test_readiness_error():
    # Mock get_db
    def override_get_db():
        class MockDB:
            def execute(self, *args, **kwargs):
                raise Exception("DB Error")
        yield MockDB()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "error", "message": "database unavailable"}
    finally:
        app.dependency_overrides.pop(get_db, None)
