from __future__ import annotations

import logging
import time

import jwt

from app.config import Settings
from app.exceptions import GitHubAuthError
from app.github.client import AsyncGitHubClient

logger = logging.getLogger(__name__)


class GitHubAuth:
    """Handles GitHub App JWT generation and installation access token exchange."""

    def __init__(self, settings: Settings):
        self.app_id = settings.github_app_id
        self.private_key = settings.github_private_key

    def create_jwt(self, ttl_seconds: int = 540) -> str:
        """Create a GitHub App JWT valid for ``ttl_seconds`` (default 9 minutes)."""
        now = int(time.time())
        payload = {
            "iat": now - 30,
            "exp": now + ttl_seconds,
            "iss": self.app_id,
        }
        try:
            token = jwt.encode(payload, self.private_key, algorithm="RS256")
        except Exception as exc:
            logger.error("Failed to encode GitHub App JWT: %s", type(exc).__name__)
            raise GitHubAuthError("Failed to encode GitHub App JWT.") from exc

        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return token

    async def get_installation_token(self, installation_id: int) -> str:
        """Exchange the App JWT for a short-lived installation access token."""
        app_jwt = self.create_jwt()
        async with AsyncGitHubClient(jwt_token=app_jwt) as client:
            try:
                data = await client.post(
                    f"/app/installations/{installation_id}/access_tokens",
                    json={},
                )
            except Exception as exc:
                logger.error(
                    "Failed to exchange JWT for installation token: %s", type(exc).__name__
                )
                raise GitHubAuthError(
                    f"Failed to obtain installation token for {installation_id}."
                ) from exc

        token = data.get("token")
        if not token or not isinstance(token, str):
            raise GitHubAuthError("GitHub response did not contain a valid installation token.")
        return token

    async def get_client_for_installation(self, installation_id: int) -> AsyncGitHubClient:
        """Return an opened GitHub API client authenticated for the given installation."""
        token = await self.get_installation_token(installation_id)
        client = AsyncGitHubClient(installation_token=token)
        await client.__aenter__()
        return client
