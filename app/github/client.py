from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.exceptions import GitHubAPIError

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class AsyncGitHubClient:
    """Async GitHub API client with timeouts and retry logic."""

    def __init__(
        self,
        *,
        installation_token: str | None = None,
        jwt_token: str | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings
        self.installation_token = installation_token
        self.jwt_token = jwt_token
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = self._build_client()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_client(self) -> httpx.AsyncClient:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.installation_token:
            headers["Authorization"] = f"Bearer {self.installation_token}"
        elif self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"

        timeout = self.settings.github_request_timeout if self.settings else 30
        return httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise GitHubAPIError("GitHub client is not opened as a context manager.")
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        **kwargs,
    ) -> Any:
        client = self._get_client()
        settings = self.settings
        max_retries = settings.github_max_retries if settings else 3
        backoff = settings.github_retry_backoff if settings else 1.0

        last_exception: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = await client.request(method, path, json=json, params=params, **kwargs)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_exception = exc
                status = exc.response.status_code
                if status in {403, 404, 422} or attempt >= max_retries:
                    raise GitHubAPIError(
                        f"GitHub API {method} {path} returned {status}.",
                        details={"status": status, "body": exc.response.text[:500]},
                    ) from exc
                logger.warning(
                    "GitHub API %s %s returned %s, retrying (attempt %d/%d)",
                    method,
                    path,
                    status,
                    attempt + 1,
                    max_retries,
                )
            except httpx.TimeoutException as exc:
                last_exception = exc
                if attempt >= max_retries:
                    raise GitHubAPIError(
                        f"GitHub API {method} {path} timed out."
                    ) from exc
                logger.warning(
                    "GitHub API %s %s timed out, retrying (attempt %d/%d)",
                    method,
                    path,
                    attempt + 1,
                    max_retries,
                )
            except httpx.RequestError as exc:
                last_exception = exc
                if attempt >= max_retries:
                    raise GitHubAPIError(
                        f"GitHub API {method} {path} request error."
                    ) from exc
                logger.warning(
                    "GitHub API %s %s request error: %s, retrying",
                    method,
                    path,
                    type(exc).__name__,
                )

            if attempt < max_retries:
                await self._sleep(backoff * (2 ** attempt))

        raise GitHubAPIError(
            f"GitHub API {method} {path} failed after retries."
        ) from last_exception

    @staticmethod
    async def _sleep(seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)

    async def get(self, path: str, **kwargs) -> Any:
        return await self._request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> Any:
        return await self._request("POST", path, **kwargs)

    # Convenience helpers

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        return await self.get(f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls/{pr_number}")

    async def list_pull_request_files(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = 1
        per_page = 100
        while True:
            page_files = await self.get(
                f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls/{pr_number}/files",
                params={"per_page": per_page, "page": page},
            )
            if not isinstance(page_files, list):
                raise GitHubAPIError("GitHub did not return a list of PR files.")
            files.extend(page_files)
            if not page_files:
                break
            page += 1
        return files

    async def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        body: str,
        event: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self.post(
            f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls/{pr_number}/reviews",
            json={
                "body": body,
                "event": event,
                "comments": comments,
            },
        )


def split_repo_full_name(full_name: str) -> tuple[str, str]:
    """Split ``owner/repo`` into (owner, repo)."""
    parts = full_name.split("/")
    if len(parts) != 2 or not all(parts):
        raise GitHubAPIError(f"Invalid repository full name: {full_name!r}")
    return parts[0], parts[1]
