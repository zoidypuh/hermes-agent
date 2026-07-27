# Mem0 Memory Provider

Semantic memory via Mem0 Platform, Mem0 OSS, or a local Mem0 REST service.

The provider is deliberately conservative:

- `mem0_add` stores explicit facts verbatim with `infer=false`.
- Completed chat turns are not ingested by default.
- Live prompt injection is optional and hard-capped to 3 memories.
- Live injection only uses memories marked `metadata.source=explicit`.
- Recent memories and same-session echoes are blocked before injection.
- Injection decisions are written to an audit JSONL file.

## Requirements

- `pip install mem0ai`
- Mem0 API key from [app.mem0.ai](https://app.mem0.ai)

## Setup

```bash
hermes memory setup    # select "mem0"
```

Or manually:
```bash
hermes config set memory.provider mem0
echo "MEM0_API_KEY=your-key" >> ~/.hermes/.env
```

## Config

Behavioral settings live in `$HERMES_HOME/mem0.json` (set them via `hermes memory setup`). Only the secret `MEM0_API_KEY` belongs in `~/.hermes/.env`.

| Key | Default | Description |
|-----|---------|-------------|
| `mode` | `platform` | `platform` (Mem0 Cloud), `oss` (self-managed, in-process), or `local` (explicit REST mode) |
| `host` | — | Self-hosted Mem0 server URL (the Docker dashboard). When set, connects over HTTP with `X-API-Key`. Don't combine with `mode: oss` |
| `user_id` | `hermes-user` | User identifier on Mem0 |
| `agent_id` | `hermes` | Agent identifier |
| `rerank` | `false` | Rerank search results for relevance (platform mode only) |
| `base_url` | — | Required for `local` mode, e.g. `http://127.0.0.1:8888` |
| `read_user_ids` | — | Optional list/comma-separated read pool. Defaults to `user_id`. |
| `write_enabled` | `true` | Allows `mem0_add` explicit writes. |
| `auto_inject_enabled` | `true` | Enables prefetch context injection. Set `false` for tools-only memory. |
| `prefetch_top_k` | `3` | Max live-injected memories. Clamped to 3. |
| `search_top_k` | `10` | Candidate search count before injection filters. |
| `rerank_threshold` | `0.7` | Minimum score for live injection. |
| `auto_add_enabled` | `false` | Allows turn-sync candidate ingestion only with `inference_enabled=true` and `candidate_user_id`. |
| `inference_enabled` | `false` | Allows Mem0 extraction for candidate ingestion. |
| `candidate_user_id` | — | Non-injected namespace for auto-inferred pending candidates. |
| `candidate_read_enabled` | `false` | Include the candidate namespace in manual `mem0_search`; live injection still blocks `source=inferred`. |
| `audit_log_enabled` | `true` | Writes live-injection audit records. |
| `audit_log_path` | `$HERMES_HOME/logs/mem0_live_injection_audit.jsonl` | Override audit log path. |

The plugin has four connection modes:

- **Platform** — Mem0's hosted cloud (`api.mem0.ai`). Set `MEM0_API_KEY`. (default)
- **Self-hosted dashboard** — a Mem0 server you run yourself via Docker. Set `host`. See below.
- **Local REST** — an explicitly selected REST service. Set `mode: local` and `base_url`. See below.
- **OSS** — run Mem0 in-process with your own LLM + vector store. Set `mode: oss`. See below.

## Self-Hosted Dashboard (Server) Mode

Connect the plugin to a standalone Mem0 server you run yourself — the Docker-shipped Mem0 dashboard/server with its own REST API. Unlike OSS mode (which runs `mem0ai` in-process with your own vector store), here the plugin just talks HTTP to your server.

1. Run the Mem0 server (FastAPI + pgvector) from its Docker image and note its URL and `ADMIN_API_KEY`.
2. Point the plugin at it — via the setup wizard:
   ```bash
   hermes memory setup    # select "mem0" → "Self-hosted server"
   # Or non-interactive:
   hermes memory setup mem0 --mode selfhosted --host http://localhost:8888 --api-key your-admin-api-key
   ```
   or via env vars:
   ```bash
   echo "MEM0_HOST=http://localhost:8888" >> ~/.hermes/.env
   echo "MEM0_API_KEY=your-admin-api-key" >> ~/.hermes/.env
   ```
   or in `$HERMES_HOME/mem0.json`:
   ```json
   {
     "host": "http://localhost:8888",
     "api_key": "your-admin-api-key"
   }
   ```
3. Start a fresh Hermes session and call `mem0_search` — it connects to your server.

The plugin authenticates with `X-API-Key` and uses the server's `/search` and `/memories` routes. `api_key` is optional — omit it only for servers running with `AUTH_DISABLED`.

> Setting `host` routes to the self-hosted server automatically. Don't set `mode: oss` — OSS takes precedence and ignores `host`.

## Local REST Mode

For the local Mem0 API stack:

```json
{
  "mode": "local",
  "base_url": "http://127.0.0.1:8888",
  "api_key": "m0sk_...",
  "user_id": "gismar",
  "auto_inject_enabled": true,
  "prefetch_top_k": 3
}
```

The provider sends the API key as `X-API-Key`.

## Live Injection Guardrails

Prefetch search can return many candidates, but only memories passing all of these are injected:

- `metadata.source` is exactly `explicit`
- score is at or above `rerank_threshold`
- `created_at` / `metadata.created_at_iso` is at least 10 minutes old
- same-session memories are at least 1 hour old
- final injected count is at most 3, or lower if `prefetch_top_k` is set below 3

Audit records have this shape:

```json
{
  "timestamp": "2026-06-25T00:00:00+00:00",
  "inject": true,
  "reason": "inject",
  "turn": 7,
  "mode": "auto",
  "session": "session-id",
  "query": "user message",
  "candidates": [
    {"id": "m1", "text": "fact", "score": 0.91, "created_at": "...", "source": "explicit", "age_seconds": 1200, "reason": "inject"}
  ],
  "injected": ["m1"]
}
```

Possible `reason` values include `inject`, `empty`, `recency_block`, `same_session_block`, `inferred_block`, `below_threshold`, and `provider_error`.

## OSS (Self-Hosted) Mode

Run Mem0 locally with your own LLM, embedder, and vector store. This is the in-process SDK mode. To instead connect to a Mem0 server you run via Docker, see [Self-Hosted Dashboard (Server) Mode](#self-hosted-dashboard-server-mode) above.

### Interactive Setup

```bash
hermes memory setup
# Select "mem0" → "Open Source (self-hosted)"
# Follow prompts for LLM, embedder, and vector store
```

### Agent-Driven Setup (Flags)

```bash
hermes memory setup mem0 --mode oss \
  --oss-llm openai --oss-llm-key sk-... \
  --oss-vector qdrant
```

### Supported Providers

| Component | Providers |
|-----------|-----------|
| LLM | openai, ollama |
| Embedder | openai, ollama |
| Vector Store | qdrant (local/server), pgvector |

### Flags Reference

| Flag | Description |
|------|-------------|
| `--mode` | `platform` or `oss` |
| `--oss-llm` | LLM provider (default: openai) |
| `--oss-llm-key` | LLM API key |
| `--oss-embedder` | Embedder provider (default: openai) |
| `--oss-vector` | Vector store (default: qdrant) |
| `--oss-vector-path` | Qdrant local path |
| `--user-id` | User identifier |

## Switching Modes

### Platform to OSS

```bash
hermes memory setup mem0 --mode oss --oss-llm-key sk-...
```

Or edit `$HERMES_HOME/mem0.json` directly:
```json
{
  "mode": "oss",
  "oss": {
    "llm": {"provider": "openai", "config": {"model": "gpt-5-mini"}},
    "embedder": {"provider": "openai", "config": {"model": "text-embedding-3-small"}},
    "vector_store": {"provider": "qdrant", "config": {"path": "~/.hermes/mem0_qdrant"}}
  }
}
```

### OSS to Platform

```bash
hermes memory setup mem0 --mode platform --api-key sk-...
```

### Dry Run (preview without writing)

```bash
hermes memory setup mem0 --mode oss --oss-llm-key sk-... --dry-run
```

## Tools

| Tool | Description |
|------|-------------|
| `mem0_search` | Semantic search by meaning |
| `mem0_add` | Store a fact verbatim (no LLM extraction) |
| `mem0_update` | Update a memory's text by ID |
| `mem0_delete` | Delete a memory by ID |

## Troubleshooting

### "Mem0 temporarily unavailable"

Circuit breaker tripped after 5 consecutive failures. Resets after 2 minutes.

- **Platform mode**: Check API key and internet connectivity.
- **OSS mode**: Check that your vector store (qdrant/pgvector) is running.

### OSS: Qdrant connection refused

```bash
# If using local Qdrant, check the storage path is writable:
ls -la ~/.hermes/mem0_qdrant

# If using Qdrant server, check it's reachable:
curl http://localhost:6333/healthz
```

### OSS: PGVector connection refused

```bash
# Verify PostgreSQL is running and accepting connections:
pg_isready -h localhost -p 5432
```

### OSS: Ollama not reachable

```bash
# Check Ollama is running:
curl http://localhost:11434/api/tags
```

### Memories not appearing

- `mem0_add` stores verbatim (no extraction). Auto extraction is disabled unless `auto_add_enabled=true`, `inference_enabled=true`, and `candidate_user_id` is configured.
- Search uses semantic matching — try broader queries.
- Check `user_id` matches between sessions (`$HERMES_HOME/mem0.json`).
- If `auto_inject_enabled=false`, memories remain available through `mem0_search` but are not injected into the prompt.
- If `metadata.source` is missing or not `explicit`, the memory is searchable but not live-injected.
