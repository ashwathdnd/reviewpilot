# ReviewPilot Implementation Plan

## Goal
Build a production-quality vertical slice of an AI-powered GitHub pull request reviewer. The app is installed as a GitHub App, receives webhook events, fetches PR data, runs deterministic static checks plus an LLM review, and publishes a single `COMMENT` review with inline comments.

## Environment Notes
- The target stack is Python 3.12; the local machine currently has Python 3.9.6.
- Development will use the available interpreter, and `Dockerfile` will pin `python:3.12-slim` for production parity.
- Package versions are already pinned in `requirements.txt`.

## Implementation Stages

### 1. Project Layout
Create `app/` and `tests/` directories and empty `__init__.py` files where needed:

```
reviewpilot/
  app/
    __init__.py
    main.py
    config.py
    models.py
    database.py
    exceptions.py
    github/
      __init__.py
      auth.py
      client.py
      diff_parser.py
      publisher.py
    review/
      __init__.py
      engine.py
      prompts.py
      static_rules.py
      validator.py
    api/
      __init__.py
      webhooks.py
      health.py
  tests/
    conftest.py
    test_webhook.py
    test_diff_parser.py
    test_validator.py
    test_static_rules.py
    test_github_client.py
    test_publisher.py
```

### 2. Configuration (`app/config.py`)
Pydantic-settings model loading from `.env` and environment variables:
- `github_app_id`, `github_private_key` (PEM content or path), `github_webhook_secret`
- `openai_api_key`, `openai_model`
- `database_url` (SQLite default)
- `confidence_threshold`, `max_findings`, `max_findings_per_file`, `max_files`, `max_chars`
- Timeouts and retry counts for GitHub / OpenAI
- `log_level`

### 3. Domain Models (`app/models.py`)
- Enums: `Category`, `Severity`, `RiskLevel`
- Pydantic `Finding` and `ReviewResult` models with validation.
- SQLAlchemy ORM models: `ReviewRun`, `FindingRecord`, `WebhookDelivery`.

### 4. Database (`app/database.py`)
- SQLAlchemy engine/session factory for SQLite.
- `get_db` dependency and `init_db` helper.
- Idempotency storage keyed by `X-GitHub-Delivery`.

### 5. GitHub Authentication (`app/github/auth.py`)
- Load private key from config (path or PEM string).
- Generate RS256 JWT valid for ~9 minutes using PyJWT.
- Exchange JWT for installation access token via `POST /app/installations/{id}/access_tokens`.

### 6. GitHub API Client (`app/github/client.py`)
- `AsyncGitHubClient` with httpx, timeouts, retries for transient HTTP errors.
- Methods: `get_installation_token`, `get_pull_request`, `list_pull_request_files` (paginated), `create_pull_request_review`.
- Helpers to build repository URLs from webhook payload.

### 7. Diff Parser (`app/github/diff_parser.py`)
- Parse GitHub file objects (`filename`, `status`, `patch`, `changes`, etc.).
- Build per-file line maps from unified-diff hunks (right/new side line numbers).
- Filter ignored files: lock files, `dist/`, `build/`, `vendor/`, `node_modules/`, generated/minified/binary files, files without patches.
- Enforce `max_files` and `max_chars` budget.

### 8. Static Rules (`app/review/static_rules.py`)
- Deterministic checks over changed lines using the same `Finding` model.
- Detect: hardcoded secrets/API keys, `eval` usage, empty exception handlers, debug statements, new `TODO`/`FIXME` comments, source changes without test changes.

### 9. LLM Review Engine (`app/review/engine.py` + `prompts.py`)
- Build a structured prompt from PR metadata and filtered diff.
- Call OpenAI with `response_format` JSON schema matching `ReviewResult`.
- Combine LLM output with static findings.

### 10. Validator (`app/review/validator.py`)
- Filter by `confidence >= threshold`.
- Ensure `file_path` is in the changed file set.
- Validate `line` against the right-side diff line map.
- Deduplicate by `(file_path, line, category, title)`.
- Rank by severity + confidence, then apply global max and per-file max.

### 11. Publisher (`app/github/publisher.py`)
- Build review body: heading, risk level, summary, severity counts, suggested tests, disclaimer.
- Split findings into inline comments (valid line) and body-only findings.
- Call GitHub create review API with `event=COMMENT`.

### 12. API (`app/api/webhooks.py`, `app/api/health.py`, `app/main.py`)
- `GET /health` health check.
- `POST /webhook`:
  - Verify `X-Hub-Signature-256` HMAC-SHA256.
  - Read `X-GitHub-Delivery` and reject duplicates.
  - Filter `pull_request.opened`, `synchronize`, `reopened` actions.
  - Enqueue review work via `BackgroundTasks` so the webhook returns immediately.
- `app/main.py` wires everything and creates DB tables on startup.

### 13. Tests (`tests/`)
- `conftest.py`: shared fixtures, test settings, in-memory DB.
- `test_webhook.py`: valid/invalid signatures, idempotency, unsupported events, fast response.
- `test_diff_parser.py`: ignored file patterns, line-number extraction, patch parsing.
- `test_validator.py`: confidence filter, duplicates, max per file, invalid line fallback.
- `test_static_rules.py`: secret, eval, empty except, debug, TODO, missing tests.
- `test_github_client.py`: retry behavior, URL helpers (mocked network).
- `test_publisher.py`: review body formatting and inline/body split.

### 14. Packaging
- `.env.example` with all required variables.
- `.gitignore` for `.venv`, `__pycache__`, `*.db`, `.env`, keys.
- `Dockerfile` multi-stage-friendly single-stage build on `python:3.12-slim`.
- `docker-compose.yml` with env-file mounting.
- Update `requirements.txt` if new packages are needed.

### 15. README
Exact instructions for:
- Creating the GitHub App, permissions, subscribed events, private key, install.
- `.env` setup.
- Local tunnel (ngrok/localtunnel).
- Running locally and with Docker.
- Running tests.
- Opening a sample PR and troubleshooting.

### 16. Final Verification
- Run `pytest` and fix any failures.
- Run `python -m compileall app tests`.
- Grep for TODO/placeholder methods.
- Update README to match implementation.
- Start a local tunnel and print its URL for GitHub App webhook configuration.
