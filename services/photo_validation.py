import io
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
