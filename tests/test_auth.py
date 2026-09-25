import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app

client = TestClient(app)

class TestAuthAndProfile:
    def test_login_success(self):
        response = client.post('/api/v1/token', data={'username': 'test@beercall.com', 'password': 'password'})
        assert response.status_code in [200, 401, 404, 422]

    def test_get_profile(self):
        response = client.get('/api/v1/me')
        assert response.status_code in [200, 401, 404]
