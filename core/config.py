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

BEERCALL_E2E_GAME = os.getenv("BEERCALL_E2E_GAME")
E2E_GAME_IDS = frozenset({
    "AVATAR_ROULETTE", "HOT_POTATO", "BRAIN_DUEL", "DEATH_FINGER",
    "BARMAN_EQUILIBRISTE", "MAX_PRESSURE", "PENALTY_SHOOTOUT",
    "PHOTO_CHALLENGE", "DRUNKEN_DRAWING",
})
if BEERCALL_E2E_GAME is not None and BEERCALL_E2E_GAME not in E2E_GAME_IDS:
    raise ValueError("BEERCALL_E2E_GAME must name a playable mini-game")
