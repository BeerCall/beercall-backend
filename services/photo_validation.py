import io
import math

from PIL import Image

_model = None

def get_detector():
    global _model
    if _model is None:
        from ultralytics import YOLO
        _model = YOLO('yolov8n.pt')
    return _model

# IDs des classes dans le dataset COCO pour les boissons
DRINK_CLASS_IDS = [39, 40, 41, 45]  # 39: bottle, 41: cup, 45: bowl (souvent confondu avec un verre large)


# On peut aussi ajouter 40: wine glass si nécessaire

def is_drink_detected(file_bytes: bytes, detector=None) -> bool:
    """
    Analyse réelle de l'image via YOLOv8 pour détecter une boisson.
    Entièrement gratuit et local.
    """
    try:
        # Convertir les bytes en image PIL
        image = Image.open(io.BytesIO(file_bytes))

        if detector is None:
            detector = get_detector()

        # Exécuter la détection
        # conf=0.25 est le seuil de confiance (25%)
        results = detector(image, conf=0.25, verbose=False)

        for result in results:
            # On vérifie si l'une des boîtes de détection appartient aux classes cibles
            for box in result.boxes:
                class_id = int(box.cls[0])
                if class_id in DRINK_CLASS_IDS:
                    return True

        return False
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
