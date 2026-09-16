# Deployment Guide: Multi-Step Research Agent

## 1. Overview

This guide covers deploying the Multi-Step Research Agent -- a FastAPI application backed by PostgreSQL, orchestrated by LangGraph, and traced with Langfuse. The agent uses OpenAI GPT-4 for reasoning and Tavily for web search. Everything runs containerized via Docker.

**Stack:**
- **Runtime:** Python 3.14, FastAPI, Uvicorn
- **Database:** PostgreSQL 16
- **Agent Framework:** LangGraph
- **LLM:** OpenAI GPT-4
- **Search:** Tavily
- **Tracing:** Langfuse

---

## 2. Prerequisites

- **Docker Desktop** installed and running
- **API Keys** ready:
  - `OPENAI_API_KEY` -- from [OpenAI Platform](https://platform.openai.com/api-keys)
  - `TAVILY_API_KEY` -- from [Tavily](https://tavily.com/)
  - `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` -- from [Langfuse](https://langfuse.com/)
- **PostgreSQL connection string** -- local or managed instance
- **Git** for version control

---

## 3. Docker Deployment (Local)

### 3a. Build the Image

```bash
docker build -t multi-step-agent .
```

### 3b. docker-compose.yml

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:16
    container_name: agent-postgres
    restart: unless-stopped
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: agentuser
      POSTGRES_PASSWORD: agentpassword
      POSTGRES_DB: agentdb
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agentuser -d agentdb"]
      interval: 5s
      timeout: 5s
      retries: 5

  agent:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: multi-step-agent
    restart: unless-stopped
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql://agentuser:agentpassword@postgres:5432/agentdb

volumes:
  postgres_data:
    driver: local
```

### 3c. Start Everything

```bash
docker-compose up --build -d
```

### 3d. Verify

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "healthy", "database": "connected"}
```

---

## 4. Dockerfile

```dockerfile
# Stage 1: Build dependencies
FROM python:3.14-slim AS builder

WORKDIR /app

RUN pip install poetry

COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.in-project true && \
    poetry install --with dev --no-interaction --no-ansi

# Stage 2: Runtime
FROM python:3.14-slim

WORKDIR /app

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

COPY --from=builder /app/.venv /app/.venv
COPY . .

ENV PATH="/app/.venv/bin:$PATH"

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 5. Deploy to Railway

### Step 1: Push to GitHub

```bash
git add .
git commit -m "Initial deployment"
git push origin main
```

### Step 2: Create Railway Project

1. Go to [railway.app](https://railway.app)
2. Click **New Project** > **Deploy from GitHub Repo**
3. Select your repository

### Step 3: Add PostgreSQL

1. In the project dashboard, click **New** > **Database** > **PostgreSQL**
2. Railway provisions a managed PostgreSQL instance automatically
3. The `DATABASE_URL` environment variable is set automatically on the PostgreSQL service -- no manual entry needed

### Step 4: Add the Agent Service

1. Click **New** > **Service** > **GitHub Repo**
2. Select your repo
3. Railway auto-detects the Dockerfile
4. Set the service name (e.g., `agent-api`)

### Step 5: Set Environment Variables

Go to the agent service > **Variables** tab and add:

```
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxx
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_HOST=https://cloud.langfuse.com
COST_BUDGET_PER_REQUEST=0.50
MAX_ITERATIONS=10
```

> **Note:** Railway does not auto-set `DATABASE_URL` on the agent service. Reference the PostgreSQL service's `DATABASE_URL` by using `${{Postgres.DATABASE_URL}}` in the agent service variables.

### Step 6: Deploy

Railway auto-deploys on push. Trigger a manual deploy from the dashboard or wait for the next push.

### Step 7: Verify

```bash
curl https://your-service-name.up.railway.app/health
```

---

## 6. Deploy to Render

### Step 1: Push to GitHub

```bash
git add .
git commit -m "Initial deployment"
git push origin main
```

### Step 2: Create Web Service

1. Go to [render.com](https://render.com)
2. Click **New** > **Web Service**
3. Connect your GitHub repository

### Step 3: Configure the Service

- **Name:** `multi-step-agent`
- **Runtime:** Docker
- **Dockerfile Path:** `Dockerfile`
- **Port:** `8000`
- **Health Check Path:** `/health`

### Step 4: Add Managed PostgreSQL

1. Go back to the Render dashboard
2. Click **New** > **PostgreSQL**
3. Name it (e.g., `agent-db`)
4. Copy the **Internal Database URL** once provisioned

### Step 5: Set Environment Variables

On the Web Service > **Environment** tab:

```
DATABASE_URL=<paste Internal Database URL from Render PostgreSQL>
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxx
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_HOST=https://cloud.langfuse.com
COST_BUDGET_PER_REQUEST=0.50
MAX_ITERATIONS=10
```

### Step 6: Deploy

Click **Create Web Service**. Render builds the Docker image and starts the service.

### Step 7: Verify

```bash
curl https://your-service-name.onrender.com/health
```

---

## 7. Environment Variables

| Variable | Description | Required | Railway | Render |
|---|---|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | Yes | Auto-set by Postgres plugin | Manual from managed DB |
| `OPENAI_API_KEY` | OpenAI API key for GPT-4 | Yes | Manual | Manual |
| `TAVILY_API_KEY` | Tavily search API key | Yes | Manual | Manual |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key for tracing | Yes | Manual | Manual |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key for tracing | Yes | Manual | Manual |
| `LANGFUSE_HOST` | Langfuse host URL | No | Defaults to cloud | Defaults to cloud |
| `COST_BUDGET_PER_REQUEST` | Max cost per agent request in USD | No | Manual | Manual |
| `MAX_ITERATIONS` | Max agent loop iterations | No | Manual | Manual |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated) | No | Manual | Manual |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | No | Manual | Manual |

---

## 8. Scaling Considerations

### Horizontal Scaling

Multiple replicas behind a load balancer require connection pooling. Use PgBouncer to avoid exhausting PostgreSQL connections:

- Each replica opens multiple connections to the database
- Default PostgreSQL `max_connections` is ~100
- PgBouncer sits between replicas and PostgreSQL, multiplexing connections

**Render:** No native PgBouncer -- use a connection pool mode in your app (e.g., SQLAlchemy pool) or an external pooler.

**Railway:** Can deploy PgBouncer as a separate service (e.g., `edoburu/pgbouncer` image) and point `DATABASE_URL` through it.

### Background Workers vs API Mode

- **API mode:** Serves requests synchronously. Suitable for low-to-medium traffic.
- **Worker mode:** Offload long-running agent tasks to a background queue (e.g., Celery + Redis). The API enqueues tasks, workers process them, results are returned via polling or webhook.

For multi-step agents that can take 10-30 seconds per request, worker mode prevents request timeouts.

### Rate Limiting

Implement rate limiting at the platform level or in your FastAPI middleware:

```python
# Using slowapi
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.get("/query")
@limiter.limit("10/minute")
async def query_agent(request: Request, ...):
    ...
```

---

## 9. Monitoring in Production

### Langfuse Trace Review

1. Log in to [Langfuse](https://cloud.langfuse.com)
2. Navigate to **Traces** to see every agent execution
3. Inspect individual steps, token usage, latency, and errors
4. Use **Sessions** to group traces by user or request batch

### Health Endpoint

Use an external uptime monitor (e.g., UptimeRobot, Better Uptime, Railway's built-in health checks) to poll:

```
GET /health
```

Configure alerts if the endpoint returns non-200 or is unreachable.

### Log Aggregation

- **Railway:** Logs are available in the service dashboard under the **Logs** tab. Use `railway logs` CLI for streaming.
- **Render:** Logs are in the service dashboard under **Logs**. Supports log drains to external services.
- **Recommended:** Forward logs to a centralized service (Datadog, Axiom, or similar) for search and alerting.

### Alerting on Error Spikes

Set up alerts when:
- Error rate exceeds 5% of requests in a 5-minute window
- P95 latency exceeds 30 seconds (agent timeout threshold)
- Health check fails for 3 consecutive checks
- Langfuse reports an anomaly in token usage or cost

---

## 10. Rollback and Version Control

### Railway

1. Go to the service in the Railway dashboard
2. Click **Deployments** tab
3. Find the last known-good deployment
4. Click **Rollback to this version**

Railway keeps a full deployment history for every service.

### Render

1. Go to the service in the Render dashboard
2. Click **Events** tab
3. Find the successful deployment checkpoint
4. Click **Rollback to this version**

Render preserves deployment history per service.

### Tagged Releases

Use semantic versioning for all production releases:

```bash
git tag -a v1.0.0 -m "Production release: initial deployment"
git push origin v1.0.0
```

Tag commits so rollbacks reference a known state. Platform-specific rollback still uses the deployment history, but tags give you a human-readable audit trail.

---

## 11. Production Checklist

- [ ] API keys (`OPENAI_API_KEY`, `TAVILY_API_KEY`, `LANGFUSE_*`) are environment variables, not committed to source
- [ ] `DATABASE_URL` uses a managed PostgreSQL instance (not local Docker volume in production)
- [ ] Langfuse correctly configured with valid `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`
- [ ] CORS configured for your frontend domain only (not `*`)
- [ ] Health check endpoint (`/health`) responds with 200
- [ ] `COST_BUDGET_PER_REQUEST` set to cap per-request spending
- [ ] `MAX_ITERATIONS` set to prevent infinite agent loops
- [ ] Logs visible in the platform dashboard (Railway/Render)
- [ ] Dockerfile uses multi-stage build with non-root user
- [ ] PostgreSQL data is backed up (managed instances handle this automatically)
- [ ] Rate limiting enabled on public endpoints
- [ ] Error alerting configured for error spikes
