from __future__ import annotations

"""Application-specific exceptions."""


class ReviewPilotError(Exception):
    """Base class for all application errors."""

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(ReviewPilotError):
    """Raised when required configuration is missing or invalid."""


class WebhookValidationError(ReviewPilotError):
    """Raised when a webhook payload fails signature or delivery validation."""


class GitHubAPIError(ReviewPilotError):
    """Raised when a GitHub API call fails."""


class GitHubAuthError(ReviewPilotError):
    """Raised when GitHub App authentication fails."""


class LLMError(ReviewPilotError):
    """Raised when the LLM integration fails or returns invalid output."""


class ReviewError(ReviewPilotError):
    """Raised when review processing or publishing fails."""
