"""
Unit tests for the pure functions in auth/service.py — no DB, no HTTP.

`register_user`, `authenticate_user`, and `get_current_user` all need a real
database session (they run SQL queries directly), so they belong in an
integration test instead, once tests/integration/conftest.py's Postgres
fixture is in play. Everything tested here needs nothing but the functions
themselves.
"""
from datetime import timedelta

import pytest
from jose import JWTError, jwt

from src.auth.service import create_access_token, hash_password, verify_password
from src.config import settings


class TestPasswordHashing:
    def test_hash_is_not_the_plain_password(self):
        hashed = hash_password("correct horse battery staple")
        assert hashed != "correct horse battery staple"

    def test_correct_password_verifies(self):
        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed) is True

    def test_wrong_password_does_not_verify(self):
        hashed = hash_password("correct horse battery staple")
        assert verify_password("wrong guess", hashed) is False

    def test_same_password_hashed_twice_produces_different_hashes(self):
        # bcrypt salts each hash randomly — this is what stops an attacker
        # from spotting two users who share a password just by comparing hashes.
        first = hash_password("same-password")
        second = hash_password("same-password")
        assert first != second
        assert verify_password("same-password", first) is True
        assert verify_password("same-password", second) is True


class TestAccessToken:
    def test_token_contains_subject_and_expiry(self):
        token = create_access_token(subject="user-123")
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])

        assert payload["sub"] == "user-123"
        assert "exp" in payload

    def test_token_respects_custom_expiry(self):
        token = create_access_token(subject="user-123", expires_delta=timedelta(minutes=5))
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])

        # exp is a Unix timestamp; just confirm it's roughly 5 minutes out,
        # not asserting an exact second to avoid a flaky test on slow CI.
        from datetime import datetime, timezone
        expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        assert timedelta(minutes=4) < (expires_at - now) < timedelta(minutes=6)

    def test_tampered_token_fails_to_decode(self):
        token = create_access_token(subject="user-123")
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

        with pytest.raises(JWTError):
            jwt.decode(tampered, settings.secret_key, algorithms=[settings.algorithm])