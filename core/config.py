import os

def get_secret_key() -> str:
    secret = os.getenv("BEERCALL_SECRET_KEY")
    if not secret:
        raise ValueError("BEERCALL_SECRET_KEY is missing")
    return secret

SECRET_KEY = get_secret_key()

APP_ENV = os.getenv("APP_ENV", "development")
PHOTO_DETECTOR_MODE = os.getenv("PHOTO_DETECTOR_MODE", "yolo")


def validate_photo_detector_config() -> None:
    if APP_ENV not in {"development", "test", "production"}:
        raise ValueError("APP_ENV must be development, test or production")
    if PHOTO_DETECTOR_MODE not in {"yolo", "always_accept"}:
        raise ValueError("PHOTO_DETECTOR_MODE must be yolo or always_accept")
    if PHOTO_DETECTOR_MODE == "always_accept" and APP_ENV != "test":
        raise ValueError("PHOTO_DETECTOR_MODE=always_accept requires APP_ENV=test")


validate_photo_detector_config()
