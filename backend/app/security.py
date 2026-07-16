import hashlib
import hmac
import secrets


def generate_scanner_token() -> str:
    return secrets.token_urlsafe(32)


def hash_scanner_token(token: str, salt: str) -> str:
    payload = f"{salt}:{token}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_scanner_token(token: str, token_hash: str, salt: str) -> bool:
    candidate = hash_scanner_token(token, salt)
    return hmac.compare_digest(candidate, token_hash)

