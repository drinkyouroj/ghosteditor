import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def generate_token() -> str:
    """Generate a cryptographically random 32-byte hex token."""
    return secrets.token_hex(32)


def hash_token(token: str) -> str:
    """Hash a token with SHA-256 for storage. Tokens are never stored in plaintext."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(
    user_id: str,
    token_version: int,
    is_provisional: bool = False,
    expires_delta: timedelta | None = None,
) -> str:
    if expires_delta is None:
        if is_provisional:
            expires_delta = timedelta(minutes=settings.provisional_token_expire_minutes)
        else:
            expires_delta = timedelta(minutes=settings.access_token_expire_minutes)

    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {
        "sub": user_id,
        "type": "provisional" if is_provisional else "full",
        "token_version": token_version,
        "exp": expire,
    }
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, token_version: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    to_encode = {
        "sub": user_id,
        "type": "refresh",
        "token_version": token_version,
        "exp": expire,
    }
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT. Returns claims dict or None if invalid."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None
