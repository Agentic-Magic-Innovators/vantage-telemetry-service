# vantage-telemetry-service

Telemetry ingestion, idempotency, and metrics query API for the Vantage split stack.

## Run locally

```powershell
cd D:\root\projects\vantage-harness-db
docker compose up -d
python scripts\apply_migrations.py postgresql://vantage:vantage@localhost:5432/vantage_migration

cd D:\root\projects\vantage-telemetry-service
$env:VANTAGE_TELEMETRY_PORT = "50224"
$env:DATABASE_URL = "postgresql://vantage:vantage@localhost:5432/vantage_migration"
$env:TELEMETRY_INTERNAL_KEY = "dev-internal-key"
$env:TELEMETRY_BRIDGE_JWT_SECRET = "dev-bridge-secret"
python -m app.main
```

## Docker (standalone deploy)

```powershell
copy .env.example .env
docker compose up -d --build
```

Bundled UI: http://localhost:50224/dashboard

Full guide: [vantage-platform/DOCKER.md](../vantage-platform/DOCKER.md)

## Google Sign-In (User tab)

The OAuth client must list **every origin** where the dashboard is opened. The old monolith only registered `http://localhost:50123`; the split stack adds new ports.

In [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → your OAuth 2.0 Client ID → **Authorized JavaScript origins**, add:

| Origin | Service |
|--------|---------|
| `http://localhost:50224` | Telemetry bundled UI (this service) |
| `http://localhost:50223` | Harness-core bundled UI |
| `http://localhost:50225` | Harness-ui (embeds this dashboard in iframe) |

Save and wait ~1 minute, then hard-refresh the dashboard.

**Until origins are updated:** set `VANTAGE_UI_ALLOW_EMAIL_LOGIN=true` (default in platform compose) and sign in on the User tab with an email from `allowedEmails` in `config/telemetry.config.json`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/telemetry` | MCP / client telemetry ingest |
| POST | `/v1/webhooks/github` | GitHub PR lifecycle webhooks → Delivery & PRs |
| POST | `/v1/internal/events` | Harness-core inference metrics |
| GET | `/metrics` | Dashboard summary (auth required) |
| GET | `/dashboard` | Standalone telemetry UI |

## GitHub webhook (Delivery & PRs)

Automatically populate the **Delivery & PRs** dashboard with real PR metrics (reviews, files changed, cycle time) by pointing a GitHub repository webhook at telemetry-service.

### 1. Configure the service

```powershell
# In .env or docker-compose environment
GITHUB_WEBHOOK_SECRET=<random-secret-from-secrets.token_hex(32)>
GITHUB_DEFAULT_TEAM_ID=engineering   # optional — stamped on webhook events
```

### 2. Add webhook in GitHub

Repository → **Settings** → **Webhooks** → **Add webhook**

| Field | Value |
|-------|--------|
| Payload URL | `https://your-telemetry-host/v1/webhooks/github` |
| Content type | `application/json` |
| Secret | Same value as `GITHUB_WEBHOOK_SECRET` |
| Events | **Pull requests** and **Pull request reviews** |

For local dev, expose port 50224 with [ngrok](https://ngrok.com/) or similar, or set `VANTAGE_ALLOW_UNSIGNED_WEBHOOKS=true` for unsigned test POSTs only.

### 3. What gets logged

| GitHub event | Telemetry type | Dashboard fields |
|--------------|----------------|------------------|
| PR opened | `pr_opened` | Files, lines added/deleted, openedAt |
| PR synchronize | `pr_updated` | Updated file counts |
| Review submitted | `pr_reviewed` | Reviews count, reviewState |
| PR merged | `pr_merged` | commitHash, mergedAt → cycle time |
| PR closed (not merged) | `pr_closed` | Status |

Future PRs will appear automatically — no MCP manual logging required.
