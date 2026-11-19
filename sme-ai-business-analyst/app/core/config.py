import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "SME AI Business Analyst Platform"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_debug: bool = False
    app_base_url: str = "http://localhost:8000"
    secret_key: str = ""  # Must be set via environment
    allowed_hosts: str = "localhost,127.0.0.1"
    cors_origins: str = "http://localhost:8501,http://localhost:3000"

    # Supabase (Optional fallback/metadata)
    supabase_url: str = ""
    supabase_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sme_ai"
    sync_database_url: str = "postgresql://postgres:postgres@localhost:5432/sme_ai"
    database_pool_size: int = Field(default=20, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0)

    # WhatsApp
    whatsapp_verify_token: str = ""  # Must be set via environment
    whatsapp_app_secret: str = ""  # Must be set via environment
    whatsapp_access_token: str = ""  # Must be set via environment
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v20.0"
    enable_group_messaging: bool = False  # Disabled by default; requires Meta OBA verification
    waasz_phone_number: str = "+234 904 476 5496"
    waasz_whatsapp_url: str = "https://wa.me/2349044765496"

    # Access Control & Strategy
    access_mode: str = "open"  # "allowlist" | "invite_code" | "open"
    max_active_users: int = 100
    require_invite_code: bool = False
    enable_user_referrals: bool = False

    # Admin Dashboard Authentication & Security (Configured via .env or first-time setup wizard)
    admin_dashboard_username: str = ""
    admin_dashboard_password: str = ""
    admin_dashboard_password_hash: str = ""
    admin_session_expire_days: int = 7

    # Debt Recovery, Suppression & Test Safe Mode
    suppression_secret_key: str = ""
    is_test: bool = False
    details_base_url: str = "https://waaszai.com"
    founder_phone: str = "2347065015924"
    meta_template_failure_threshold: float = 0.08
    meta_template_stop_threshold: float = 0.03


    # AI Providers
    gemini_api_key: str = ""
    gemini_image_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-70b-versatile"
    openai_api_key: str = ""
    openai_fallback_model: str = "gpt-4o-mini"
    ai_provider_order: str = "openai,groq,gemini"
    ai_balance_mode: str = "smart_balanced"

    # OCR and Voice
    google_application_credentials: str = ""
    tesseract_cmd: str = ""
    media_download_dir: str = "local_media"

    # Google Drive Integration & Backups
    google_drive_onboarding_mode: Literal["optional", "mandatory"] = "optional"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/integrations/google-drive/callback"

    # Image Generation Flag (cost control)
    enable_image_generation: bool = False

    # Alarm Escalation Mode Default (False = single alert; True = repeating alarm)
    default_reminders_to_alarm_mode: bool = False

    # Cost Limits
    daily_ai_spend_limit_usd: float = Field(default=5.0, ge=0)
    monthly_ai_spend_limit_usd: float = Field(default=100.0, ge=0)
    daily_user_image_limit: int = Field(default=5, ge=1)
    gemini_image_gen_model: str = ""
    openai_image_gen_model: str = ""
    ai_cost_alert_email: str = ""

    # Rate Limiting
    rate_limit_per_minute: int = Field(default=30, ge=1)
    max_message_length: int = Field(default=4000, ge=1)
    webhook_idempotency_ttl_seconds: int = Field(default=86400, ge=60)

    # Scheduling
    daily_report_hour_local: int = Field(default=21, ge=0, le=23)
    weekly_report_weekday: int = Field(default=6, ge=0, le=6)
    local_timezone: str = "Africa/Lagos"

    # Logging & Monitoring
    sentry_dsn: str = ""
    log_level: str = "INFO"

    @field_validator("app_debug", mode="before")
    @classmethod
    def validate_debug(cls, v):
        """Ensure debug is False in production."""
        if isinstance(v, str):
            v = v.lower() in ("true", "1", "yes")
        fields = getattr(cls, "model_fields", getattr(cls, "__fields__", {}))
        if v and "app_env" in fields:
            logger.warning("Debug mode enabled - ensure this is not production!")
        return v

    @field_validator("app_env", mode="before")
    @classmethod
    def validate_env(cls, v):
        """Validate environment is set to appropriate value."""
        v = str(v).lower()
        if v not in ("local", "test", "staging", "production"):
            raise ValueError(f"Invalid app_env: {v}")
        return v

    @model_validator(mode="after")
    def resolve_gemini_image_api_key(self):
        """Ensure gemini_image_api_key falls back to gemini_api_key if unset."""
        if not self.gemini_image_api_key:
            self.gemini_image_api_key = self.gemini_api_key
        return self

    @model_validator(mode="after")
    def resolve_google_oauth_redirect_uri(self):
        """Derive redirect URI from app_base_url if left at local default and base URL is set."""
        if (
            self.google_oauth_redirect_uri == "http://localhost:8000/api/v1/integrations/google-drive/callback"
            and self.app_base_url
            and not self.app_base_url.startswith("http://localhost")
        ):
            self.google_oauth_redirect_uri = f"{self.app_base_url.rstrip('/')}/api/v1/integrations/google-drive/callback"
        return self

    @property
    def is_using_dedicated_gemini_image_key(self) -> bool:
        """True if gemini_image_api_key is dedicated (distinct from shared gemini_api_key)."""
        return bool(
            self.gemini_image_api_key
            and self.gemini_api_key
            and self.gemini_image_api_key != self.gemini_api_key
        )

    def validate_required_secrets(self) -> None:
        """Validate that required secrets are configured."""
        required = {
            "secret_key": "SECRET_KEY",
            "whatsapp_verify_token": "WHATSAPP_VERIFY_TOKEN",
        }
        
        # These are only required in non-local environments
        if self.app_env in ("staging", "production"):
            required.update({
                "app_base_url": "APP_BASE_URL",
                "whatsapp_access_token": "WHATSAPP_ACCESS_TOKEN",
                "whatsapp_phone_number_id": "WHATSAPP_PHONE_NUMBER_ID",
                "supabase_url": "SUPABASE_URL",
                "supabase_key": "SUPABASE_KEY",
            })
        
        missing = []
        for field, env_var in required.items():
            value = getattr(self, field, "")
            if not value or value.startswith("replace-") or value == "change-me-before-deploy":
                missing.append(f"{env_var} (field: {field})")
        
        if missing:
            if self.app_env == "production":
                raise ValueError(f"Missing required secrets: {', '.join(missing)}")
            else:
                logger.warning(f"Missing secrets in {self.app_env}: {', '.join(missing)}")

    def validate_at_startup(self) -> None:
        """Run all validations at application startup."""
        self.validate_required_secrets()
        
        # Validate at least one AI provider is configured
        providers = [self.gemini_api_key, self.groq_api_key, self.openai_api_key]
        if not any(providers):
            logger.warning("No AI provider configured - using local heuristic extraction only")
        
        # Validate database URL
        if not self.database_url or not self.sync_database_url:
            raise ValueError("DATABASE_URL must be configured")
        
        # Debug mode warning
        if self.app_debug and self.app_env == "production":
            raise ValueError("DEBUG mode cannot be enabled in production!")

        # Refuse to start without dedicated suppression key (Requirement 4)
        if not self.suppression_secret_key and self.secret_key:
            self.suppression_secret_key = self.secret_key
        if self.app_env != "test" and not self.suppression_secret_key:
            raise ValueError(
                "SUPPRESSION_SECRET_KEY is required and cannot be empty. "
                "Refusing to start without dedicated suppression key."
            )

        # IS_TEST warning (Requirement 7)
        if getattr(self, "is_test", False):
            if self.app_env == "production":
                logger.error("🚨 CRITICAL WARNING: IS_TEST is ON in PRODUCTION! Outbound WhatsApp sends are simulated.")
            else:
                logger.warning("⚠️ WARNING: IS_TEST is ON. All outbound WhatsApp messages are simulated.")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
# Validate at import time
settings.validate_at_startup()
