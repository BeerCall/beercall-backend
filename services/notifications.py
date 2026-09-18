import logging
from typing import List, Optional

import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger("beercall_notifications")
logger.setLevel(logging.INFO)

try:
    cred = credentials.Certificate("firebase-credentials.json")
    firebase_admin.initialize_app(cred)
except ValueError:
    logger.info("Firebase est déjà initialisé")
except FileNotFoundError:
    logger.warning("Fichier firebase-credentials.json introuvable")


def send_push_notifications(tokens: List[str], title: str, body: str, data: Optional[dict] = None):
    valid_tokens = [token for token in tokens if token]
    for offset in range(0, len(valid_tokens), 500):
        batch = valid_tokens[offset:offset + 500]
        try:
            message = messaging.MulticastMessage(
                notification=messaging.Notification(title=title, body=body),
                data={str(k): str(v) for k, v in (data or {}).items()},
                tokens=batch,
            )
            messaging.send_each_for_multicast(message)
        except Exception:
            logger.exception("Erreur Firebase pour le lot de tokens %s-%s", offset, offset + len(batch))


def notify_scheduled_apero(tokens: List[str], squad_id: int, apero_id: int, location: str, scheduled_for: str):
    send_push_notifications(
        tokens, 
        "🚨 ALERTE SOIF : TRAQUENARD PROGRAMMÉ !", 
        f"Échauffe ton foie ! Un apéro est prévu à {location}. T'as intérêt à être là, on accepte plus l'excuse d'aqua-poney !", 
        {"type": "scheduled_apero", "squad_id": squad_id, "apero_id": apero_id, "scheduled_for": scheduled_for}
    )


def notify_started_scheduled_apero(tokens: List[str], squad_id: int, apero_id: int, location: str):
    send_push_notifications(
        tokens, 
        "🍻 C'EST PARTI MON KIKI !", 
        f"Le premier verre est déjà servi à {location}. Ramène ton cul avant qu'on finisse le fût !", 
        {"type": "scheduled_apero_started", "squad_id": squad_id, "apero_id": apero_id}
    )
