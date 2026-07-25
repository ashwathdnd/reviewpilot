# ReviewPilot

ReviewPilot is an AI-powered GitHub App that reviews pull requests. It listens for `pull_request.opened`, `pull_request.synchronize`, and `pull_request.reopened` events, validates webhook signatures, fetches the PR diff, runs deterministic static checks, calls an LLM for structured review output, and publishes a single `COMMENT` review with inline comments.

## Table of Contents

- [What it does](#what-it-does)
- [Prerequisites](#prerequisites)
- [Create a GitHub App](#create-a-github-app)
- [Install the app](#install-the-app)
- [Configure ReviewPilot](#configure-reviewpilot)
- [Expose localhost](#expose-localhost)
- [Run locally](#run-locally)
- [Run with Docker](#run-with-docker)
- [Run tests](#run-tests)
- [Open a sample pull request](#open-a-sample-pull-request)
- [Troubleshooting webhook delivery](#troubleshooting-webhook-delivery)
- [Project structure](#project-structure)
- [Design notes](#design-notes)
- [Limitations](#limitations)

## What it does

1. Receives GitHub webhook events and validates `X-Hub-Signature-256` (HMAC SHA-256).
2. Ignores duplicate deliveries using the `X-GitHub-Delivery` header.
3. Generates a GitHub App JWT and exchanges it for an installation access token.
4. Fetches PR metadata and all changed files (paginated).
5. Filters out generated, binary, vendor, build, and lock files.
6. Combines patches while respecting file and character limits.
7. Runs deterministic static checks (hardcoded secrets, `eval`, empty exception handlers, debug statements, TODO/FIXME, missing tests).
8. Sends the PR context and diff to an OpenAI model with a JSON-schema-structured response.
9. Validates, deduplicates, ranks, and limits findings by confidence, line validity, and per-file caps.
10. Publishes one PR review with an overall summary and inline comments (falling back to the review body when line mapping is unavailable).
11. Persists the review run and findings in SQLite.

## Prerequisites

- Python 3.12+ (the local development environment here uses Python 3.9.6; the `Dockerfile` pins Python 3.12)
- GitHub account
- OpenAI API key
- A tunneling tool such as [ngrok](https://ngrok.com/) or [localtunnel](https://localtunnel.me/)

## Create a GitHub App

1. Go to **Settings** → **Developer settings** → **GitHub Apps** → **New GitHub App**.
2. Fill in:
   - **GitHub App name**: `ReviewPilot` (must be unique across GitHub).
   - **Homepage URL**: `https://github.com/your-org/reviewpilot`.
   - **Webhook URL**: leave this for now; you will set it after exposing localhost.
   - **Webhook secret**: generate a strong random string (e.g., `openssl rand -hex 32`). Save it as `GITHUB_WEBHOOK_SECRET`.
3. Under **Permissions**, set:
   - **Repository permissions**
     - Pull requests: **Read & write**
     - Contents: **Read-only**
   - **Organization permissions**: none required.
   - **Account permissions**: none required.
4. Under **Subscribe to events**, check:
   - Pull request
5. Under **Where can this GitHub App be installed?**, choose **Any account** or **Only on this account** depending on your needs.
6. Click **Create GitHub App**.
7. Note the **App ID** shown on the app page. Save it as `GITHUB_APP_ID`.
8. Scroll to **Private keys** and click **Generate a private key**. A `.pem` file downloads. Save its contents as `GITHUB_PRIVATE_KEY`.

## Install the app

1. On the app page, click **Install App**.
2. Choose the account/organization and select the repositories you want ReviewPilot to review.
3. Click **Install**.
4. Note the **Installation ID** from the URL (`https://github.com/settings/installations/<INSTALLATION_ID>`). You will see it in webhook payloads automatically.

## Configure ReviewPilot

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` and set:
   - `GITHUB_APP_ID` — numeric App ID.
   - `GITHUB_PRIVATE_KEY` — PEM content (paste the full key including `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----`). You can also set it to a path to the `.pem` file.
   - `GITHUB_WEBHOOK_SECRET` — the secret from the GitHub App settings.
   - `OPENAI_API_KEY` — your OpenAI API key.
   - `OPENAI_MODEL` — default is `gpt-4o-mini`.
3. Review the optional tuning variables (`CONFIDENCE_THRESHOLD`, `MAX_FINDINGS`, `MAX_FILES`, etc.) and adjust if needed.

## Expose localhost

GitHub needs a public HTTPS URL to deliver webhooks. Use a tunnel while developing locally.

### Option A: ngrok

```bash
ngrok http 8000
```

Copy the **HTTPS** URL (e.g., `https://abc123.ngrok.io`) and append `/webhook`.

### Option B: localtunnel

```bash
npx localtunnel --port 8000
```

Copy the URL it gives you and append `/webhook`.

### Register the webhook URL

1. Go to your GitHub App settings.
2. Set **Webhook URL** to `https://<your-tunnel-domain>/webhook`.
3. Ensure **Webhook secret** matches `GITHUB_WEBHOOK_SECRET`.
4. Click **Save changes**.

## Run locally

Create and activate a virtual environment (Python 3.12 recommended):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then start the server:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The health endpoint is available at `http://localhost:8000/health` and the webhook endpoint at `http://localhost:8000/webhook`.

## Run with Docker

1. Build the image:
   ```bash
   docker build -t reviewpilot .
   ```
2. Run the container:
   ```bash
   docker run -p 8000:8000 --env-file .env reviewpilot
   ```

### Docker Compose

```bash
docker compose up --build
```

The database file is mounted at `./reviewpilot.db` so it persists across container restarts.

## Run tests

```bash
source .venv/bin/activate
pytest tests -v
```

The suite includes tests for:
- valid and invalid webhook signatures,
- idempotent webhook delivery handling,
- unified diff line-number extraction,
- ignored file rules,
- confidence filtering,
- duplicate and per-file finding limits,
- invalid line numbers falling back to the review body,
- GitHub client retry logic and URL helpers.

GitHub and OpenAI network calls are mocked in tests.

## Open a sample pull request

1. Create a new branch in a repository where ReviewPilot is installed.
2. Make a small change, such as adding a `TODO` comment or leaving a debug `print()` statement.
3. Open a pull request.
4. ReviewPilot should receive the webhook, process it in the background, and publish a `COMMENT` review.
5. Check the GitHub App **Advanced** tab for webhook delivery logs.

## Troubleshooting webhook delivery

| Symptom | Likely cause | Fix |
|--------|--------------|-----|
| `400 Bad Request` / `Invalid JSON` | Payload is malformed | Check the request body in the GitHub delivery log. |
| `401 Unauthorized` | Signature mismatch | Verify `GITHUB_WEBHOOK_SECRET` matches the GitHub App setting. Ensure the secret is the raw string, not a hex digest. |
| `200 ignored` | Unsupported event or action | Confirm the event is `pull_request` and the action is `opened`, `synchronize`, or `reopened`. |
| `200 duplicate delivery` | Same `X-GitHub-Delivery` sent twice | This is expected; GitHub may retry. |
| No review appears | Background task failed | Check the application logs for errors. Verify `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`, and `OPENAI_API_KEY`. |
| Review appears but no inline comments | Line number could not be mapped to the right side of the diff | The finding is included in the review body instead. |

### Quick checks

- Verify the server is reachable:
  ```bash
  curl http://localhost:8000/health
  ```
- Verify the tunnel URL forwards to `/webhook`:
  ```bash
  curl -X POST https://<your-tunnel-domain>/webhook -H "Content-Type: application/json" -d '{}'
  ```
  You should receive a `401 Unauthorized` or `200 ignored` response.

## Project structure

```
reviewpilot/
  app/
    main.py                 # FastAPI app and lifespan
    config.py               # Pydantic settings
    models.py               # Pydantic and SQLAlchemy models
    database.py             # SQLite persistence
    exceptions.py           # Typed exceptions
    services.py             # Webhook signature and payload helpers
    github/
      auth.py               # GitHub App JWT and installation token
      client.py             # Async GitHub API client with retries
      diff_parser.py        # Unified diff parser and ignore rules
      publisher.py          # PR review publisher
    review/
      engine.py             # LLM review engine
      prompts.py            # Prompts
      static_rules.py       # Deterministic checks
      validator.py          # Finding validation, deduplication, ranking
    api/
      webhooks.py           # Webhook endpoint
      health.py             # Health endpoint
  tests/
    conftest.py
    test_webhook.py
    test_diff_parser.py
    test_validator.py
    test_static_rules.py
    test_github_client.py
    test_publisher.py
  .env.example
  .gitignore
  Dockerfile
  docker-compose.yml
  requirements.txt
  README.md
```

## Design notes

- **Idempotency**: every webhook is stored keyed by `X-GitHub-Delivery`. Duplicate deliveries are rejected before any GitHub API calls are made.
- **Fast response**: the webhook handler returns immediately after queueing the review via FastAPI `BackgroundTasks`.
- **Security**: webhook signatures are verified with HMAC SHA-256; private keys and tokens are never logged.
- **Diff handling**: a real unified-diff parser maps new/right-side line numbers so inline comments are only submitted when valid.
- **Static + LLM**: deterministic checks catch common issues quickly; the LLM provides structured, higher-level review output. Both use the same `Finding` model.
- **Validation**: findings are filtered by confidence, changed file membership, valid line numbers, deduplication, global max, and per-file max.
- **Persistence**: SQLite stores review runs, findings, and webhook delivery status.

## Limitations

- Local development uses Python 3.9.6 because it is the interpreter available in this environment. The `Dockerfile` targets Python 3.12.
- SQLite is used for persistence; it is suitable for a single instance but not for horizontal scaling. To scale, replace with a shared database.
- The LLM integration is OpenAI-only in this version.
- Background tasks are used for asynchronous processing; for high throughput or durable queues, consider a task queue such as Celery or RQ.
