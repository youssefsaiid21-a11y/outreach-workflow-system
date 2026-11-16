"""
Symmetric encryption for sensitive values stored in the database (e.g. IMAP passwords).

Key source priority:
1. FERNET_KEY env var  -  a 32-byte URL-safe base64 Fernet key.
   Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
2. If not set, derives a deterministic key from SECRET_KEY env var.
3. If neither is set, raises RuntimeError  -  the system refuses to start without an encryption key.

The module is safe to import even if the cryptography package is absent  -  callers should
check `ENCRYPTION_AVAILABLE` before using encrypt/decrypt.
"""
import os
import base64
import functools
import logging

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False
    logger.error(
        "The 'cryptography' package is not installed. "
        "IMAP passwords CANNOT be encrypted at rest. "
        "Run: pip install cryptography"
    )


@functools.lru_cache(maxsize=1)
def _get_fernet() -> "Fernet":
    """Build (and cache) the Fernet instance for the configured key.

    P1-4 / deep audit MEDIUM-6: without this cache every encrypt/decrypt
    call recomputed the PBKDF2 480k-iteration key derivation, which
    dominated IMAP password decryption latency. The cache is keyed on
    nothing (maxsize=1)  -  if FERNET_KEY or SECRET_KEY is rotated, the
    process must be restarted, which is already the documented workflow."""
    raw = os.getenv("FERNET_KEY")
    if raw:
        return Fernet(raw.encode() if isinstance(raw, str) else raw)

    secret = os.getenv("SECRET_KEY")
    if secret:
        import hashlib
        # SWAT-H7: salt is now configurable via FERNET_SALT env var
        salt = os.getenv("FERNET_SALT", "outreach-system-fernet-salt").encode()
        dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, 480_000)
        key = base64.urlsafe_b64encode(dk)
        return Fernet(key)

    # No encryption key configured  -  refuse to start in any environment
    logger.critical(
        "Neither FERNET_KEY nor SECRET_KEY is set. "
        "Cannot encrypt/decrypt sensitive values. Set FERNET_KEY or SECRET_KEY in .env."
    )
    raise RuntimeError(
        "Neither FERNET_KEY nor SECRET_KEY is set. "
        "Refusing to start without an encryption key. Set FERNET_KEY or SECRET_KEY in .env."
    )


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return a URL-safe base64 token."""
    if not ENCRYPTION_AVAILABLE:
        raise RuntimeError("cryptography package is not installed.")
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by encrypt(). Raises ValueError on bad token."""
    if not ENCRYPTION_AVAILABLE:
        raise RuntimeError("cryptography package is not installed.")
    f = _get_fernet()
    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Could not decrypt value  -  wrong key or corrupted token.") from e
