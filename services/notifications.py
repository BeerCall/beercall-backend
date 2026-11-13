import logging
from dataclasses import dataclass
from typing import List, Literal, Optional

import firebase_admin
from firebase_admin import credentials, exceptions, messaging

logger = logging.getLogger("beercall_notifications")
logger.setLevel(logging.INFO)

import os

def initialize_firebase():
    firebase_path = os.getenv("FIREBASE_CREDENTIALS_PATH")
    if not firebase_path:
        logger.info("Firebase disabled: FIREBASE_CREDENTIALS_PATH is not set")
        return False
    
    if not os.path.exists(firebase_path):
        raise FileNotFoundError(f"Firebase credentials not found at: {firebase_path}")

    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(firebase_path)
            firebase_admin.initialize_app(cred)
        return True
    except Exception as e:
        raise ValueError(f"Failed to initialize Firebase: {e}")

FIREBASE_ENABLED = initialize_firebase()


PushStatus = Literal["accepted", "rejected"]


@dataclass(frozen=True)
class PushOutcome:
    """Result for one durable push recipient.

    - accepted: Firebase accepted the message.
    - rejected: terminal for this message/recipient; the same request must not be
      retried, without concluding that the token is permanently invalid.
    """

    status: PushStatus
    reason: Optional[str] = None


class PushRetryableError(RuntimeError):
    """Delivery may succeed later (transient, quota, authorization/config).

    The event must stay pending so the relay can retry only the recipients that
    are still open. The token is never classified or purged here.
    """

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def send_durable_push(token: str, event_id: str) -> PushOutcome:
    """Send one durable push and classify the Firebase result per recipient.

    Raises PushRetryableError for errors that may resolve later. Permanent
    request rejections are returned as PushOutcome(rejected) without touching
    the stored token.
    """
    if not FIREBASE_ENABLED:
        raise PushRetryableError("firebase_disabled", "Firebase is disabled; notification remains queued")
    try:
        messaging.send(messaging.Message(
            notification=messaging.Notification(
                title="Nouveau Beer Call ! 🍻", body="Un apéro t'attend !"
            ),
            data={"event_id": event_id},
            token=token,
        ))
    except messaging.UnregisteredError:
        logger.warning("Push rejected: token no longer registered (recipient kept in place, not purged)")
        return PushOutcome("rejected", "unregistered")
    except messaging.SenderIdMismatchError:
        logger.warning("Push rejected: token belongs to another sender (recipient kept in place, not purged)")
        return PushOutcome("rejected", "sender_mismatch")
    except exceptions.InvalidArgumentError:
        # Payload or parameters can trigger this; it is not proof that the token is invalid.
        logger.warning("Push rejected as invalid request; token validity is not concluded")
        return PushOutcome("rejected", "invalid_request")
    except exceptions.PermissionDeniedError:
        logger.warning("Push authorization refused; recipient kept pending for retry")
        raise PushRetryableError("authorization", "Permission denied by the push service")
    except exceptions.FirebaseError as exc:
        logger.warning("Push delivery failed (retryable Firebase error): %s", type(exc).__name__)
        raise PushRetryableError("firebase_error", type(exc).__name__) from exc
    return PushOutcome("accepted")


def send_push_notifications(tokens: List[str], title: str, body: str, data: Optional[dict] = None):
    if not FIREBASE_ENABLED:
        logger.info(f"Firebase disabled, ignoring notification: {title}")
        return

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
