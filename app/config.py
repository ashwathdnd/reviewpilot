from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.exceptions import ConfigurationError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    github_app_id: str = Field(..., description="GitHub App ID (numeric string).")
    github_private_key: str = Field(
        ...,
        description="GitHub App private key: PEM content or path to a .pem file.",
    )
    github_webhook_secret: str = Field(..., description="Webhook secret from GitHub App settings.")

    openai_api_key: str = Field(..., description="OpenAI API key.")
    openai_model: str = Field("gpt-4o-mini", description="OpenAI chat model to use.")
    openai_timeout: int = Field(60, ge=5, le=300, description="Timeout in seconds for OpenAI requests.")

    database_url: str = Field("sqlite:///./reviewpilot.db", description="SQLAlchemy database URL.")

    confidence_threshold: float = Field(0.7, ge=0.0, le=1.0, description="Minimum confidence for a finding.")
    max_findings: int = Field(10, ge=1, le=20, description="Maximum findings published in one review.")
    max_findings_per_file: int = Field(5, ge=1, le=10, description="Maximum findings per file.")
    max_files: int = Field(20, ge=1, le=100, description="Maximum changed files to review.")
    max_chars: int = Field(100_000, ge=1_000, le=500_000, description="Maximum characters of patch text to send to the LLM.")

    github_request_timeout: int = Field(30, ge=5, le=120, description="Timeout in seconds for GitHub API requests.")
    github_max_retries: int = Field(3, ge=0, le=10, description="Maximum retries for transient GitHub failures.")
    github_retry_backoff: float = Field(1.0, ge=0.0, le=10.0, description="Backoff multiplier between retries.")

    critic_enabled: bool = Field(True, description="Enable the critic stage in the review pipeline.")
    critic_timeout: int = Field(60, ge=5, le=300, description="Timeout in seconds for critic LLM requests.")
    rate_limit_enabled: bool = Field(True, description="Enable rate limiting on the webhook endpoint.")
    cors_origins: str = Field(
        "http://localhost:3000,http://127.0.0.1:3000",
        description="Comma-separated list of allowed CORS origins.",
    )
    port: int = Field(8000, ge=1, le=65535, description="Port for the Uvicorn server.")
    log_level: str = Field("info", description="Uvicorn/Python log level.")

    @field_validator("github_app_id")
    @classmethod
    def _validate_app_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"\d+", value):
            raise ValueError("github_app_id must be a numeric string.")
        return value

    @field_validator("github_private_key")
    @classmethod
    def _validate_private_key(cls, value: str) -> str:
        value = value.strip()
        if "BEGIN RSA PRIVATE KEY" in value or "BEGIN PRIVATE KEY" in value:
            return value

        path = Path(value)
        if not path.exists():
            raise ValueError(
                "github_private_key must be a PEM string or a path to an existing .pem file."
            )
        key_text = path.read_text(encoding="utf-8").strip()
        if "BEGIN RSA PRIVATE KEY" not in key_text and "BEGIN PRIVATE KEY" not in key_text:
            raise ValueError("Private key file does not contain a valid PEM key.")
        return key_text

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"debug", "info", "warning", "error", "critical"}
        value = value.lower()
        if value not in allowed:
            raise ValueError(f"log_level must be one of {allowed}.")
        return value

    @model_validator(mode="after")
    def _validate_limits(self) -> "Settings":
        if self.max_findings_per_file > self.max_findings:
            raise ValueError("max_findings_per_file cannot exceed max_findings.")
        return self


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except Exception as exc:  # pragma: no cover
        raise ConfigurationError(str(exc)) from exc


def get_test_settings(**overrides) -> Settings:
    """Return settings suitable for tests with optional overrides."""
    defaults = {
        "github_app_id": "12345",
        "github_private_key": """-----BEGIN RSA PRIVATE KEY-----
MIIBOgIBAAJBALRiMLAHkwSkq3eYnL6e5Cgp/0I8QB9b2TqQvxKM6QiymL0x4kV8
jG2c2kY6B0t7c4c5X6iQ8n8m7c5n4k3j2h1g0f
-----END RSA PRIVATE KEY-----
""",
        "github_webhook_secret": "test-secret",
        "openai_api_key": "test-openai-key",
        "database_url": "sqlite:///:memory:",
        "rate_limit_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)
