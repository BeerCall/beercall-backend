import os

def get_secret_key() -> str:
    secret = os.getenv("BEERCALL_SECRET_KEY")
    if not secret:
        raise ValueError("BEERCALL_SECRET_KEY is missing")
    return secret

SECRET_KEY = get_secret_key()
