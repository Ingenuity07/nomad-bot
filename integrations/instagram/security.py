import base64
import hashlib
import logging
import secrets
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_fernet_key() -> bytes:
    """
    Retrieve or derive a 32-byte URL-safe base64-encoded key for Fernet encryption.
    Prioritizes settings.INSTAGRAM_TOKEN_ENCRYPTION_KEY; falls back to deriving
    from settings.SECRET_KEY using SHA-256.
    """
    custom_key = getattr(settings, 'INSTAGRAM_TOKEN_ENCRYPTION_KEY', None)
    if custom_key:
        key_str = str(custom_key).strip()
        # If it's already a valid 32-byte base64 string
        try:
            decoded = base64.urlsafe_b64decode(key_str.encode())
            if len(decoded) == 32:
                return key_str.encode()
        except Exception:
            pass
        # Derive 32-byte key from custom string
        digest = hashlib.sha256(key_str.encode('utf-8')).digest()
        return base64.urlsafe_b64encode(digest)

    # Deterministic fallback derived from Django SECRET_KEY
    secret = getattr(settings, 'SECRET_KEY', 'nomad-instagram-secret-fallback')
    digest = hashlib.sha256(secret.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_token(raw_token: str) -> str:
    """
    Encrypt a plaintext access token using Fernet symmetric encryption.
    """
    if not raw_token:
        return ""
    fernet = Fernet(_get_fernet_key())
    encrypted_bytes = fernet.encrypt(raw_token.encode('utf-8'))
    return encrypted_bytes.decode('utf-8')


def decrypt_token(encrypted_token: str) -> str:
    """
    Decrypt an encrypted access token using Fernet symmetric encryption.
    Raises ValueError if the token is corrupted or key mismatch occurs.
    """
    if not encrypted_token:
        return ""
    fernet = Fernet(_get_fernet_key())
    try:
        decrypted_bytes = fernet.decrypt(encrypted_token.encode('utf-8'))
        return decrypted_bytes.decode('utf-8')
    except InvalidToken as exc:
        logger.error("Failed to decrypt Instagram access token: Invalid token or key mismatch.")
        raise ValueError("Invalid encrypted token or encryption key mismatch.") from exc


def generate_oauth_state(length: int = 32) -> str:
    """
    Generate a cryptographically secure random state string for OAuth 2.0 flow.
    """
    return secrets.token_urlsafe(length)


def mask_token(token: Optional[str]) -> str:
    """
    Produce a safe masked representation of a token for display in admin or logs.
    Shows first 4 characters and last 4 characters, with 8 bullets in between.
    """
    if not token:
        return "—"
    token_str = str(token)
    if len(token_str) <= 8:
        return "••••••••"
    return f"{token_str[:4]}••••••••{token_str[-4:]}"
