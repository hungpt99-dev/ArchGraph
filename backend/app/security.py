"""Authentication, authorization primitives and secret handling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from .config import get_settings
from .models import ROLE_RANK

settings = get_settings()

ALGORITHM = "HS256"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt$%s$%s" % (
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=len(expected)
        )
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def create_access_token(subject: str, role: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
        "iss": "archgraph",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM], issuer="archgraph")
    except JWTError:
        return None


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #
API_KEY_PREFIX = "agk"


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, sha256 hash). Plaintext is shown to the user once."""
    raw = secrets.token_urlsafe(32)
    plaintext = f"{API_KEY_PREFIX}_{raw}"
    prefix = plaintext[:12]
    return plaintext, prefix, hashlib.sha256(plaintext.encode()).hexdigest()


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def has_role(user_role: str, required: str) -> bool:
    return ROLE_RANK.get(user_role, 99) <= ROLE_RANK.get(required, -1)


# --------------------------------------------------------------------------- #
# Secret encryption at rest (integration credentials)
# --------------------------------------------------------------------------- #
def _fernet() -> Fernet:
    key = settings.encryption_key
    if not key:
        # Derive a stable key from the app secret so dev installs work out of the box.
        digest = hashlib.sha256(("archgraph-enc:" + settings.secret_key).encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


# --------------------------------------------------------------------------- #
# Secret redaction (scanner + logs + AI context)
# --------------------------------------------------------------------------- #
_SECRET_KEY_RE = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|credential|authorization)\s*[:=]\s*(?:bearer\s+)?[\"']?([^\s\"',;}]{4,})"
)
_URL_CRED_RE = re.compile(r"(?P<scheme>\w+://)(?P<user>[^:/@\s]+):(?P<pw>[^@/\s]+)@")
_REDACTED = "***REDACTED***"


def redact_secrets(text: str) -> str:
    if not text:
        return text
    text = _URL_CRED_RE.sub(lambda m: f"{m.group('scheme')}{m.group('user')}:{_REDACTED}@", text)
    return _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)


# --------------------------------------------------------------------------- #
# SSRF protection
# --------------------------------------------------------------------------- #
def _ip_is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_outbound_url(url: str, allow_private: bool | None = None) -> str:
    """Reject URLs that could be used for SSRF (cloud metadata, internal ranges)."""
    if allow_private is None:
        allow_private = settings.allow_private_networks
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    if host in ("localhost", "metadata.google.internal"):
        if not allow_private:
            raise ValueError("Blocked host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if _ip_is_private(ip) and not allow_private:
                raise ValueError(f"Blocked private address for host {host}")
    except socket.gaierror:
        raise ValueError(f"Cannot resolve host {host}")
    return url


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "workspace"
