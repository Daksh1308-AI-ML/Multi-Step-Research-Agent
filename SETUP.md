# Multi-Step Research Agent — Setup Guide

This guide walks you through setting up the Multi-Step Research Agent locally.

---

## 1. Prerequisites

Install the following before proceeding:

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.14+ | [python.org](https://www.python.org/downloads/) |
| Poetry | Latest | See below |
| Docker Desktop | Latest | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) |
| Git | Latest | [git-scm.com](https://git-scm.com/downloads) |

**Install Poetry on Windows:**

```powershell
pip install poetry
```

Or use the official installer:

```powershell
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
```

After installing with the official installer, restart your terminal and verify:

```powershell
poetry --version
```

---

## 2. API Keys Required

You need API keys from the following services:

| Service | Key Name(s) | Where to Get | Cost |
|---------|-------------|--------------|------|
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) | Pay-per-use (GPT-4 tokens) |
| Tavily | `TAVILY_API_KEY` | [tavily.com](https://tavily.com/) | Free tier available (1000 req/mo) |
| Langfuse | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | [cloud.langfuse.com](https://cloud.langfuse.com/) or self-host | Free tier; optional for local dev |

---

## 3. Clone and Install

```bash
git clone <repo-url>
cd multi-step-agent
poetry install
poetry shell
```

Verify you are in the Poetry shell:

```bash
python --version  # should show Python 3.14+
```

---

## 4. Environment Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Open `.env` in your editor and set each variable:

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `OPENAI_API_KEY` | Your OpenAI API key for GPT-4 calls | Yes | — |
| `TAVILY_API_KEY` | Your Tavily API key for web search | Yes | — |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key for tracing | No | — |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key for tracing | No | — |
| `DATABASE_URL` | PostgreSQL connection string | Yes | `postgresql://agent:agent@localhost:5432/agent` |
| `AGENT_TIMEOUT` | Max seconds before an agent run is cancelled | No | `120` |
| `MAX_ITERATIONS` | Max reasoning loops per agent run | No | `10` |
| `COST_BUDGET_PER_REQUEST` | Max USD spend per single research request | No | `1.00` |

---

## 5. Database Setup

### Option A: Docker PostgreSQL (Recommended)

```bash
docker run -d --name postgres-agent \
  -e POSTGRES_USER=agent \
  -e POSTGRES_PASSWORD=agent \
  -e POSTGRES_DB=agent \
  -p 5432:5432 \
  postgres:16
```

### Option B: Local PostgreSQL

Download and install from [postgresql.org/download](https://www.postgresql.org/download/windows/).

During setup, set the port to `5432`, user to `agent`, password to `agent`, and database to `agent`.

### Verify Connection

```bash
docker exec -it postgres-agent psql -U agent -d agent -c "\dt"
```

Or with a local install:

```bash
psql -U agent -d agent -h localhost -c "\dt"
```

---

## 6. Run the Agent

Start the FastAPI server:

```bash
uvicorn src.agent.main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "ok"}
```

Test with a sample research request:

```bash
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the latest breakthroughs in fusion energy?"}'
```

---

## 7. Verify Tracing (Optional)

Langfuse traces every agent step when configured. For local development:

1. Create a free account at [cloud.langfuse.com](https://cloud.langfuse.com/).
2. Create a new project and generate API keys.
3. Add `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` to your `.env`.
4. Set `LANGFUSE_HOST` to `https://cloud.langfuse.com` (or your self-hosted URL).

To view traces, open your Langfuse dashboard after sending a request. Each research run appears as a trace with individual steps (search, parse, reason, answer).

---

## 8. Run Tests

```bash
poetry run pytest
```

With coverage:

```bash
poetry run pytest --cov=src --cov-report=html
```

Coverage report will be written to `htmlcov/index.html`.

---

## 9. Troubleshooting

| Problem | Solution |
|---------|----------|
| `poetry: command not found` | Restart your terminal after install. On Windows, ensure `%APPDATA%\Python\Scripts` is in your `PATH`. |
| Database connection refused | Confirm PostgreSQL is running: `docker ps` or check the PostgreSQL service. Verify port 5432 is free. |
| OpenAI rate limit errors | Check your usage tier at [platform.openai.com/settings/organization/limits](https://platform.openai.com/settings/organization/limits). Reduce `MAX_ITERATIONS` or add a delay. |
| Port 8000 already in use | Kill the process: `netstat -ano | findstr :8000` then `taskkill /PID <pid> /F`. Or use a different port: `--port 8001`. |
| Langfuse not connecting | Verify keys are correct and `LANGFUSE_HOST` is set. Check firewall/proxy settings. Traces are silently skipped if Langfuse is unreachable. |

---

## 10. Quick Start (TL;DR)

The five commands from zero to running:

```bash
git clone <repo-url> && cd multi-step-agent
poetry install && poetry shell
cp .env.example .env   # then fill in your API keys
docker run -d --name postgres-agent -e POSTGRES_USER=agent -e POSTGRES_PASSWORD=agent -e POSTGRES_DB=agent -p 5432:5432 postgres:16
uvicorn src.agent.main:app --reload --port 8000
```
