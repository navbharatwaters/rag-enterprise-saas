"""Credential encryption for connectors using Fernet symmetric encryption."""

import json
import logging

from cryptography.fernet import Fernet, InvalidToken

from src.core.config import settings

logger = logging.getLogger(__name__)


class EncryptionError(Exception):
    """Raised when encryption or decryption fails."""


def _get_fernet() -> Fernet:
    """Get Fernet instance from configured encryption key."""
    key = settings.CONNECTOR_ENCRYPTION_KEY
    if not key:
        raise EncryptionError(
            "CONNECTOR_ENCRYPTION_KEY is not configured. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, Exception) as e:
        raise EncryptionError(f"Invalid encryption key: {e}") from e


def encrypt_credentials(credentials: dict) -> bytes:
    """Encrypt a credentials dict to bytes for database storage.

    Args:
        credentials: Dictionary of credential key-value pairs.

    Returns:
        Encrypted bytes suitable for storing in a BYTEA column.

    Raises:
        EncryptionError: If encryption key is missing or invalid.
    """
    f = _get_fernet()
    payload = json.dumps(credentials).encode("utf-8")
    return f.encrypt(payload)


def decrypt_credentials(encrypted: bytes) -> dict:
    """Decrypt credentials from database storage.

    Args:
        encrypted: Encrypted bytes from the database.

    Returns:
        Original credentials dictionary.

    Raises:
        EncryptionError: If decryption fails (wrong key, corrupted data, etc.)
    """
    f = _get_fernet()
    try:
        decrypted = f.decrypt(encrypted)
        return json.loads(decrypted.decode("utf-8"))
    except InvalidToken:
        raise EncryptionError(
            "Failed to decrypt credentials. The encryption key may have changed."
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise EncryptionError(f"Decrypted data is not valid JSON: {e}") from e
