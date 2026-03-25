import io
import math

from PIL import Image
from ultralytics import YOLO

# Chargement du modèle en mémoire (le modèle 'n' pour nano est le plus rapide)
model = YOLO('yolov8n.pt')

# IDs des classes dans le dataset COCO pour les boissons
DRINK_CLASS_IDS = [39, 40, 41, 45]  # 39: bottle, 41: cup, 45: bowl (souvent confondu avec un verre large)


# On peut aussi ajouter 40: wine glass si nécessaire

async def is_drink_detected(file_bytes: bytes) -> bool:
    """
    Analyse réelle de l'image via YOLOv8 pour détecter une boisson.
    Entièrement gratuit et local.
    """
    try:
        # Convertir les bytes en image PIL
        image = Image.open(io.BytesIO(file_bytes))

        # Exécuter la détection
        # conf=0.25 est le seuil de confiance (25%)
        results = model(image, conf=0.25, verbose=False)

        for result in results:
            # On vérifie si l'une des boîtes de détection appartient aux classes cibles
            for box in result.boxes:
                class_id = int(box.cls[0])
                if class_id in DRINK_CLASS_IDS:
                    return True

        return False
    except Exception as e:
        print(f"Erreur lors de l'analyse d'image : {e}")
        # En cas d'erreur technique, on peut choisir de valider par défaut pour ne pas bloquer l'user
        return False


def calculate_geodistance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0  # Rayon de la Terre en mètres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c
