import firebase_admin
from firebase_admin import credentials, messaging
from typing import List
import logging

logger = logging.getLogger("beercall_notifications")
logger.setLevel(logging.INFO)

# Initialisation de Firebase avec ton fichier JSON sécurisé
try:
    cred = credentials.Certificate("firebase-credentials.json")
    firebase_admin.initialize_app(cred)
    logger.info("✅ Firebase Admin initialisé avec succès.")
except ValueError:
    logger.warning("⚠️ L'app Firebase est déjà initialisée.")
except FileNotFoundError:
    logger.error("❌ Fichier firebase-credentials.json introuvable !")


def send_push_notifications(tokens: List[str], title: str, body: str, data: dict = None):
    """
    Envoie une notification Push via Firebase Cloud Messaging.
    """
    # On retire les tokens vides ou invalides
    valid_tokens = [t for t in tokens if t]

    if not valid_tokens:
        logger.info("🛑 Aucun token valide à notifier.")
        return

    # Firebase limite les envois groupés à 500 tokens max par requête
    try:
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},  # Pour envoyer des infos cachées au front (ex: l'id de l'apéro)
            tokens=valid_tokens,
        )
        response = messaging.send_each_for_multicast(message)
        logger.info(f"🚀 Notifs envoyées : {response.success_count} succès, {response.failure_count} échecs.")
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'envoi de la notification : {e}")