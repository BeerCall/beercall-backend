from unittest.mock import Mock, MagicMock, patch
from types import SimpleNamespace
import os
import subprocess
import sys
import pytest
from core import config
from services import photo_validation
from services.photo_validation import is_drink_detected

def test_accept_detector_requires_test_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    for app_env, mode in (("production", "always_accept"), ("development", "always_accept"), ("invalid", "yolo"), ("test", "invalid")):
        environment = {**os.environ, "APP_ENV": app_env, "PHOTO_DETECTOR_MODE": mode}
        for module in ("core.config", "main", "workers.beer_call_worker"):
            result = subprocess.run([sys.executable, "-c", f"import {module}"], env=environment, capture_output=True, text=True)
            assert result.returncode != 0
            assert "ValueError" in result.stderr
    environment = {**os.environ, "APP_ENV": "test", "PHOTO_DETECTOR_MODE": "always_accept"}
    result = subprocess.run([sys.executable, "-c", "import core.config; assert core.config.APP_ENV == 'test'"], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    monkeypatch.setattr(config, "APP_ENV", "test")
    monkeypatch.setattr(config, "PHOTO_DETECTOR_MODE", "always_accept")
    assert is_drink_detected(b"not an image") is True


def test_test_environment_can_use_real_yolo_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_ENV", "test")
    monkeypatch.setattr(config, "PHOTO_DETECTOR_MODE", "yolo")
    monkeypatch.setattr(photo_validation, "_model", None)
    ultralytics = MagicMock()
    ultralytics.YOLO.return_value.return_value = [SimpleNamespace(boxes=[SimpleNamespace(cls=[39])])]
    with patch.dict(sys.modules, {"ultralytics": ultralytics}):
        detector = photo_validation.get_detector()
        ultralytics.YOLO.assert_not_called()
        from io import BytesIO
        from PIL import Image
        output = BytesIO()
        Image.new("RGB", (1, 1)).save(output, format="PNG")
        assert detector(output.getvalue()) is True
        ultralytics.YOLO.assert_called_once_with("yolov8n.pt")
        assert detector(output.getvalue()) is True
        ultralytics.YOLO.assert_called_once()

def test_is_drink_detected_true():
    # Mock du résultat YOLO
    box = MagicMock()
    box.cls = [39]  # Bottle
    result = MagicMock()
    result.boxes = [box]
    
    mock_detector = Mock(return_value=[result])
    
    # 1x1 pixel PNG pour ne pas lever d'erreur PIL
    valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    
    assert is_drink_detected(valid_png, detector=mock_detector) is True

def test_is_drink_detected_false():
    box = MagicMock()
    box.cls = [1]  # Person (not drink)
    result = MagicMock()
    result.boxes = [box]
    
    mock_detector = Mock(return_value=[result])
    
    valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    
    assert is_drink_detected(valid_png, detector=mock_detector) is False

def test_is_drink_detected_exception():
    mock_detector = Mock(side_effect=Exception("Test error"))
    
    valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    
    import pytest
    with pytest.raises(RuntimeError, match="Erreur technique lors de l'analyse d'image: Test error"):
        is_drink_detected(valid_png, detector=mock_detector)
