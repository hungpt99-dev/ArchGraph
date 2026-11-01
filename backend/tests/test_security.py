"""Security unit tests: SSRF, secret handling, hashing, tokens, encryption."""

from __future__ import annotations

import pytest

from app.security import (
    create_access_token,
    decode_access_token,
    decrypt_secret,
    encrypt_secret,
    generate_api_key,
    hash_api_key,
    hash_password,
    redact_secrets,
    validate_outbound_url,
    verify_password,
)


def test_password_hashing_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second  # unique salt per hash
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)
    assert first.startswith("scrypt$")


def test_jwt_roundtrip_and_tamper_rejection():
    token = create_access_token("user-1", "DEVELOPER")
    payload = decode_access_token(token)
    assert payload["sub"] == "user-1" and payload["role"] == "DEVELOPER"
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    assert decode_access_token(tampered) is None
    assert decode_access_token("not-a-token") is None


def test_api_keys_are_hashed_not_stored_plaintext():
    plaintext, prefix, digest = generate_api_key()
    assert plaintext.startswith("agk_") and prefix == plaintext[:12]
    assert digest == hash_api_key(plaintext)
    assert plaintext not in digest and len(digest) == 64


def test_secret_encryption_roundtrip():
    encrypted = encrypt_secret("ghp_super_secret_token")
    assert encrypted and "ghp_super_secret_token" not in encrypted
    assert decrypt_secret(encrypted) == "ghp_super_secret_token"
    assert decrypt_secret("garbage") is None


def test_secret_redaction_covers_urls_and_assignments():
    text = (
        "spring.datasource.password=hunter2\n"
        "api_key: sk-live-abc123456\n"
        "url=postgres://user:topsecret@db:5432/app\n"
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig\n"
    )
    redacted = redact_secrets(text)
    for secret in ("hunter2", "sk-live-abc123456", "topsecret", "eyJhbGciOiJIUzI1NiJ9"):
        assert secret not in redacted
    assert "REDACTED" in redacted


def test_ssrf_protection_blocks_metadata_and_loopback(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORKS", "false")
    from app.config import get_settings
    from app.security import get_settings as _  # noqa: F401

    get_settings.cache_clear()
    with pytest.raises(ValueError):
        validate_outbound_url("http://169.254.169.254/latest/meta-data", allow_private=False)
    with pytest.raises(ValueError):
        validate_outbound_url("http://127.0.0.1:8080/admin", allow_private=False)
    with pytest.raises(ValueError):
        validate_outbound_url("file:///etc/passwd", allow_private=False)
    assert validate_outbound_url("https://api.github.com", allow_private=False).startswith("https://")


def test_repository_credentials_encrypted_at_rest(client, admin, workspace, db):
    response = client.post(f"/api/workspaces/{workspace['id']}/repositories",
                           headers=admin["headers"],
                           json={"name": "private-repo", "url": "https://github.com/acme/private.git",
                                 "credential": "ghp_very_secret", "scan_now": False})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["credentials_configured"] is True
    assert "ghp_very_secret" not in response.text
    from app.models import Repository

    repo = db.get(Repository, body["id"])
    assert repo.credential_enc and "ghp_very_secret" not in repo.credential_enc


def test_role_hierarchy():
    from app.security import has_role

    assert has_role("SYSTEM_ADMIN", "VIEWER")
    assert has_role("ARCHITECT", "DEVELOPER")
    assert not has_role("VIEWER", "DEVELOPER")
    assert not has_role("DEVELOPER", "ARCHITECT")
