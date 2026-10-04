import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app

client = TestClient(app)

class TestAuthAndProfile:
    def test_login_success(self):
        response = client.post('/api/auth/token/', data={'username': 'test@beercall.com', 'password': 'password'})
        assert response.status_code == 401

    def test_get_profile(self):
        response = client.get('/api/auth/me/')
        assert response.status_code == 401
