import os
import pytest
from unittest import mock

def test_firebase_disabled():
    with mock.patch.dict(os.environ, {}, clear=True):
        import importlib
        import services.notifications
        importlib.reload(services.notifications)
        assert services.notifications.FIREBASE_ENABLED is False

def test_firebase_invalid_path():
    with mock.patch.dict(os.environ, {"FIREBASE_CREDENTIALS_PATH": "non_existent.json"}):
        with pytest.raises(FileNotFoundError, match="Firebase credentials not found"):
            import importlib
            import services.notifications
            importlib.reload(services.notifications)

def test_firebase_mocked_success():
    with mock.patch.dict(os.environ, {"FIREBASE_CREDENTIALS_PATH": "mocked.json"}):
        with mock.patch("os.path.exists", return_value=True):
            with mock.patch("firebase_admin.initialize_app") as mock_init:
                with mock.patch("firebase_admin.credentials.Certificate") as mock_cert:
                    with mock.patch("firebase_admin._apps", {}):
                        import importlib
                        import services.notifications
                        importlib.reload(services.notifications)
                        assert services.notifications.FIREBASE_ENABLED is True
                        mock_cert.assert_called_once_with("mocked.json")
                        mock_init.assert_called_once()
