from unittest.mock import Mock, MagicMock
from services.photo_validation import is_drink_detected

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
    
    assert is_drink_detected(valid_png, detector=mock_detector) is False
