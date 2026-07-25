from __future__ import annotations

import pytest
from httpx import Response

from app.config import get_test_settings
from app.github.client import AsyncGitHubClient, split_repo_full_name
from app.exceptions import GitHubAPIError


@pytest.mark.asyncio
async def test_split_repo_full_name():
    assert split_repo_full_name("owner/repo") == ("owner", "repo")


@pytest.mark.asyncio
async def test_split_repo_full_name_invalid():
    with pytest.raises(GitHubAPIError):
        split_repo_full_name("owner/repo/extra")


@pytest.mark.asyncio
async def test_get_pull_request_url(httpx_mock):
    settings = get_test_settings()
    client = AsyncGitHubClient(installation_token="token", settings=settings)
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/pulls/1",
        json={"number": 1, "title": "Test"},
    )
    async with client:
        data = await client.get_pull_request("owner", "repo", 1)
    assert data["number"] == 1


@pytest.mark.asyncio
async def test_retry_transient_failure(httpx_mock):
    settings = get_test_settings(github_max_retries=2, github_retry_backoff=0.0)
    client = AsyncGitHubClient(installation_token="token", settings=settings)
    httpx_mock.add_response(url="https://api.github.com/repos/owner/repo/pulls/1", status_code=500)
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/pulls/1",
        json={"number": 1},
    )
    async with client:
        data = await client.get_pull_request("owner", "repo", 1)
    assert data["number"] == 1


@pytest.mark.asyncio
async def test_no_retry_client_error(httpx_mock):
    settings = get_test_settings(github_max_retries=2)
    client = AsyncGitHubClient(installation_token="token", settings=settings)
    httpx_mock.add_response(url="https://api.github.com/repos/owner/repo/pulls/1", status_code=404)
    async with client:
        with pytest.raises(GitHubAPIError):
            await client.get_pull_request("owner", "repo", 1)


@pytest.mark.asyncio
async def test_list_pull_request_files_pagination(httpx_mock):
    settings = get_test_settings()
    client = AsyncGitHubClient(installation_token="token", settings=settings)
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/pulls/1/files?per_page=100&page=1",
        json=[{"filename": "a.py"}, {"filename": "b.py"}],
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/pulls/1/files?per_page=100&page=2",
        json=[{"filename": "c.py"}],
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/pulls/1/files?per_page=100&page=3",
        json=[],
    )
    async with client:
        files = await client.list_pull_request_files("owner", "repo", 1)
    assert [f["filename"] for f in files] == ["a.py", "b.py", "c.py"]
