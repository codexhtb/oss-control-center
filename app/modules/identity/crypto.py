"""Argon2id credentials and opaque session token primitives."""

import hashlib
import hmac
import re
import secrets
import unicodedata

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

PASSWORD_HASHER = PasswordHasher(
    type=Type.ID, time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16
)
LOGIN_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}\Z")
DUMMY_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def normalize_login(value: str) -> str:
    login = unicodedata.normalize("NFKC", value).strip().casefold()
    if not LOGIN_PATTERN.fullmatch(login):
        raise ValueError("login must have 3-64 ASCII letters, digits, dot, underscore or hyphen")
    return login


def validate_password(value: str) -> str:
    if not 1 <= len(value) <= 1024:
        raise ValueError("password must have 1-1024 characters")
    return value


def hash_password(value: str) -> str:
    return PASSWORD_HASHER.hash(validate_password(value))


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return (
            PASSWORD_HASHER.verify(password_hash or DUMMY_HASH, password)
            and password_hash is not None
        )
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def fingerprint(kind: str, value: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()
