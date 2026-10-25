import io
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from core import config

from PIL import Image

logger = logging.getLogger("photo_timing")

_model: Any = None


def _log_photo_timing(phase: str, duration_ms: float, error: Optional[str] = None) -> None:
    utc_str = datetime.now(timezone.utc).isoformat()
    pid = os.getpid()
    payload = {
        "event": "photo_timing",
        "phase": phase,
        "pid": pid,
        "utc": utc_str,
        "duration_ms": round(max(0.0, duration_ms), 2),
        "status": "error" if error else "success",
    }
    if error:
        payload["error"] = error
    logger.info(json.dumps(payload), extra=payload)


def get_yolo_model(clock: Callable[[], float] = time.monotonic) -> Any:
    global _model
    if _model is not None:
        return _model

    start = clock()
    try:
        from ultralytics import YOLO
        loaded = YOLO('yolov8n.pt')
        duration_ms = (clock() - start) * 1000.0
        _log_photo_timing("load", duration_ms, error=None)
        _model = loaded
        return _model
    except Exception as exc:
        duration_ms = (clock() - start) * 1000.0
        _log_photo_timing("load", duration_ms, error=type(exc).__name__)
        raise


def _always_accept(file_bytes: bytes) -> bool:
    return True


def get_detector() -> Callable[[bytes], bool]:
    """Select a bytes-to-verdict detector without loading YOLO at import time."""
    config.validate_photo_detector_config()
    return _always_accept if config.PHOTO_DETECTOR_MODE == "always_accept" else _detect_with_yolo


# IDs des classes dans le dataset COCO pour les boissons
DRINK_CLASS_IDS = [39, 40, 41, 45]  # 39: bottle, 41: cup, 45: bowl (souvent confondu avec un verre large)


# On peut aussi ajouter 40: wine glass si nécessaire

def _detect_with_yolo(
    file_bytes: bytes,
    detector: Any = None,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    if detector is None:
        detector = get_yolo_model(clock=clock)

    start = clock()
    try:
        with Image.open(io.BytesIO(file_bytes)) as image:
            results = detector(image, conf=0.25, verbose=False)
            has_drink = any(int(box.cls[0]) in DRINK_CLASS_IDS for result in results for box in result.boxes)
        duration_ms = (clock() - start) * 1000.0
        _log_photo_timing("inference", duration_ms, error=None)
        return has_drink
    except Exception as exc:
        duration_ms = (clock() - start) * 1000.0
        _log_photo_timing("inference", duration_ms, error=type(exc).__name__)
        raise


def is_drink_detected(
    file_bytes: bytes,
    detector: Any = None,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    try:
        if detector is not None:
            return _detect_with_yolo(file_bytes, detector=detector, clock=clock)
        target = get_detector()
        if target == _detect_with_yolo:
            return _detect_with_yolo(file_bytes, clock=clock)
        return target(file_bytes)
    except Exception as e:
        print(f"Erreur lors de l'analyse d'image : {e}")
        # En cas d'erreur technique (réseau, modèle), on lève une exception retryable
        raise RuntimeError(f"Erreur technique lors de l'analyse d'image: {e}") from e


def calculate_geodistance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0  # Rayon de la Terre en mètres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def validate_image_file(file_bytes: bytes) -> str:
    """
    Validation stricte du fichier (A04:2021) via Magic Numbers.
    Empêche les attaques d'Unrestricted File Upload (XSS/RCE).
    """
    if file_bytes.startswith(b'\xff\xd8\xff'):
        return "jpg"
    elif file_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        return "png"
    else:
        raise ValueError("Format de fichier non autorisé. Seuls les JPG et PNG authentiques sont acceptés.")


# Nom public stable utilisé par les services métier.
calculate_distance = calculate_geodistance
