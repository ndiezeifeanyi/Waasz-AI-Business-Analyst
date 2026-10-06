from datetime import UTC, datetime, timedelta
from unittest.mock import patch, MagicMock
import jwt
import pytest
import streamlit as st

from admin.auth_manager import (
    AdminAuthManager,
    MAX_FAILED_ATTEMPTS,
    format_duration,
    get_live_base_url,
    set_live_base_url,
    pwd_context,
)
from app.core.config import settings


@pytest.fixture
def auth_mgr():
    return AdminAuthManager()


def test_no_credentials_by_default(auth_mgr):
    with patch.object(auth_mgr, "get_configured_credentials", return_value=(None, None, False)):
        assert auth_mgr.has_configured_credentials() is False
        assert auth_mgr.verify_credentials("admin", "pass") is False


def test_verify_admin_credentials_success(auth_mgr):
    with patch.object(settings, "admin_dashboard_username", "my_custom_user"):
        with patch.object(settings, "admin_dashboard_password", "MyCustomSecretPass123#"):
            assert auth_mgr.has_configured_credentials() is True
            assert auth_mgr.verify_credentials("my_custom_user", "MyCustomSecretPass123#") is True


def test_verify_admin_credentials_invalid_password(auth_mgr):
    with patch.object(settings, "admin_dashboard_username", "my_custom_user"):
        with patch.object(settings, "admin_dashboard_password", "MyCustomSecretPass123#"):
            assert auth_mgr.verify_credentials("my_custom_user", "WrongPassword123!") is False


def test_verify_admin_credentials_invalid_username(auth_mgr):
    with patch.object(settings, "admin_dashboard_username", "my_custom_user"):
        with patch.object(settings, "admin_dashboard_password", "MyCustomSecretPass123#"):
            assert auth_mgr.verify_credentials("hacker_account", "MyCustomSecretPass123#") is False


def test_argon2_password_hash_verification(auth_mgr):
    secret_pass = "UltraSecureSuperAdmin999!#"
    argon_hash = pwd_context.hash(secret_pass)

    with patch.object(settings, "admin_dashboard_username", "secops_lead"):
        with patch.object(settings, "admin_dashboard_password_hash", argon_hash):
            assert auth_mgr.has_configured_credentials() is True
            assert auth_mgr.verify_credentials("secops_lead", secret_pass) is True
            assert auth_mgr.verify_credentials("secops_lead", "WrongGuess123") is False


def test_session_token_generation_and_valid_weekly_session(auth_mgr):
    username = "founder_ceo"
    token = auth_mgr.create_session_token(username)
    assert isinstance(token, str)

    payload = auth_mgr.verify_session_token(token)
    assert payload is not None
    assert payload["sub"] == username
    assert payload["role"] == "admin"
    assert "remaining_seconds" in payload
    # Must be approximately 7 days (7 * 86400 = 604800 seconds)
    assert 600000 <= payload["remaining_seconds"] <= 604800


def test_weekly_session_token_expiration_after_7_days(auth_mgr):
    username = "founder_ceo"
    now = datetime.now(UTC)
    past_exp = now - timedelta(hours=1)
    past_iat = past_exp - timedelta(days=7)
    expired_payload = {
        "sub": username,
        "role": "admin",
        "iat": int(past_iat.timestamp()),
        "exp": int(past_exp.timestamp()),
    }
    expired_token = jwt.encode(expired_payload, auth_mgr._get_signing_key(), algorithm="HS256")

    assert auth_mgr.verify_session_token(expired_token) is None


def test_tampered_session_token_rejection(auth_mgr):
    username = "legit_admin"
    token = auth_mgr.create_session_token(username)
    tampered_token = token[:-5] + "XXXXX"

    assert auth_mgr.verify_session_token(tampered_token) is None


def test_brute_force_lockout_trigger(auth_mgr):
    st.session_state.clear()

    is_locked, _ = auth_mgr.is_locked_out()
    assert is_locked is False

    with patch("time.sleep"):
        for i in range(MAX_FAILED_ATTEMPTS - 1):
            auth_mgr.record_failed_attempt()
            is_locked, _ = auth_mgr.is_locked_out()
            assert is_locked is False

        auth_mgr.record_failed_attempt()
        is_locked, remaining = auth_mgr.is_locked_out()
        assert is_locked is True
        assert remaining > 0

        auth_mgr.reset_failed_attempts()
        is_locked, _ = auth_mgr.is_locked_out()
        assert is_locked is False


def test_format_duration():
    assert format_duration(0) == "Expired"
    assert format_duration(-10) == "Expired"
    assert "6d 23h remaining" == format_duration(6 * 86400 + 23 * 3600 + 10)
    assert "4h 15m remaining" == format_duration(4 * 3600 + 15 * 60)
    assert "10m remaining" == format_duration(10 * 60 + 5)


def test_dynamic_live_base_url_resolution():
    with patch("admin.auth_manager.database_url", return_value=None):
        with patch.object(settings, "app_base_url", "https://waasz-sme-api.duckdns.org/"):
            with patch.dict("os.environ", {"APP_BASE_URL": "https://waasz-sme-api.duckdns.org/"}):
                url = get_live_base_url()
                assert url == "https://waasz-sme-api.duckdns.org"

    with patch("admin.auth_manager.database_url", return_value=None):
        saved = set_live_base_url("https://new-custom-domain.com/")
        assert saved == "https://new-custom-domain.com"
        assert settings.app_base_url == "https://new-custom-domain.com"

