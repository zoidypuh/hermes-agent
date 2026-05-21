# LCM Preflight Incremental Compaction Handoff

Task: `t_1177f6c1` - `[LCM] Wire preflight incremental compaction and enable hermes-lcm`

Branch: `feature/lcm-preflight-hermes-lcm`

## What Changed

- `agent/conversation_loop.py`
  - Added the upstream PR #20424 behavior in the refactored runtime path.
  - When the normal token threshold is not reached, Hermes now calls `context_compressor.should_compress_preflight(messages)` if the active engine exposes it.
  - If the hook returns `True`, Hermes uses the existing `_compress_context(...)` path.
  - Hook exceptions are debug-level and non-fatal.

- `tests/run_agent/test_run_agent.py`
  - Added coverage for:
    - sub-threshold `should_compress_preflight()` triggering compression,
    - false return skipping compression,
    - hook exception not breaking the turn.

- Installed user plugin:
  - `/home/gismar/.hermes/plugins/hermes-lcm`
  - Source: `https://github.com/stephenschoettler/hermes-lcm`
  - Installed revision: `71dd8a1`
  - Plugin version: `0.11.1`

- Durable Hermes default profile config:
  - `/home/gismar/.hermes/config.yaml`
    - `context.engine: lcm`
    - `compression.enabled: true`
    - `plugins.enabled` includes `hermes-lcm`
    - `plugins.enabled` no longer includes `lossless-hermes`
    - `plugins.disabled` includes `lossless-hermes`
  - `/home/gismar/.hermes/.env`
    - `LCM_DEFERRED_MAINTENANCE_ENABLED=true`
    - `LCM_LEAF_CHUNK_TOKENS=2000`
    - `LCM_FRESH_TAIL_COUNT=16`
    - `LCM_DATABASE_PATH=/home/gismar/.hermes/hermes-lcm.db`
  - Note: `/home/gismar/.hermes/lcm.db` is an older incompatible schema for
    this plugin. It was left untouched, and the default profile now uses a
    fresh `hermes-lcm.db` instead.

- Durable Mara profile config:
  - `/home/gismar/.hermes/profiles/mara-codexrt/config.yaml`
    - `context.engine: lcm`
    - `compression.enabled: true`
    - `plugins.enabled` includes `hermes-lcm`
    - `plugins.disabled` includes `lossless-hermes`
  - `/home/gismar/.hermes/profiles/mara-codexrt/.env`
    - `LCM_LEAF_CHUNK_TOKENS=2000`
    - `LCM_DEFERRED_MAINTENANCE_ENABLED=true`
    - `LCM_FRESH_TAIL_COUNT=16`

## How To Enable/Select

Default Hermes profile:

```yaml
# /home/gismar/.hermes/config.yaml
context:
  engine: lcm
plugins:
  enabled:
    - hermes-lcm
  disabled:
    - lossless-hermes
```

Default profile LCM env:

```bash
# /home/gismar/.hermes/.env
LCM_DEFERRED_MAINTENANCE_ENABLED=true
LCM_LEAF_CHUNK_TOKENS=2000
LCM_FRESH_TAIL_COUNT=16
LCM_DATABASE_PATH=/home/gismar/.hermes/hermes-lcm.db
```

Mara profile equivalents are in:

- `/home/gismar/.hermes/profiles/mara-codexrt/config.yaml`
- `/home/gismar/.hermes/profiles/mara-codexrt/.env`

After config changes, restart Hermes/gateway/proxy processes from a fresh terminal or another session. This Codex session did not restart the local proxy because restarting it from inside the routed session can terminate the session itself.

## Verification

Canonical test wrapper initially failed because the shared Hermes venv was missing `pytest-timeout`.
Installed `pytest-timeout==2.4.0` into `/home/gismar/.hermes/hermes-agent/venv`, then reran the wrapper successfully.

```bash
scripts/run_tests.sh tests/run_agent/test_run_agent.py -k 'engine_preflight'
```

Result:

```text
3 passed in 2.69s
```

```bash
scripts/run_tests.sh tests/agent/test_context_engine.py
```

Result:

```text
19 passed in 1.46s
```

Plugin selection check:

```bash
/home/gismar/.local/bin/hermes plugins list 2>/dev/null | rg 'hermes-lcm|lossless-hermes|lcm'
```

Result:

```text
hermes-lcm enabled 0.11.1
lossless-hermes disabled 1.0.0
```

Default `lcm_status` direct handler check:

```bash
HERMES_HOME=/home/gismar/.hermes PYTHONPATH=/home/gismar/.hermes/hermes-agent-lcm-preflight-hermes-lcm \
  /home/gismar/.hermes/hermes-agent/venv/bin/python <direct-lcm-status-check>
```

Result summary:

```json
{
  "ok": true,
  "runtime_identity": {
    "engine": "lcm",
    "database_path": "/home/gismar/.hermes/hermes-lcm.db",
    "plugin_name": "hermes-lcm",
    "plugin_version": "0.11.1",
    "plugin_git_commit": "71dd8a1549517c4fffa359cecd2b8dd1c5a9f341"
  },
  "config": {
    "deferred_maintenance_enabled": true,
    "fresh_tail_count": 16,
    "leaf_chunk_tokens": 2000
  },
  "threshold_tokens": 150000
}
```

Earlier default `lcm_status` check failed with:

```text
sqlite3.OperationalError: no such column: session_id
```

Cause: stale `/home/gismar/.hermes/lcm.db` schema. Resolution: keep that file
untouched and route the default profile to `/home/gismar/.hermes/hermes-lcm.db`.

Config parse check:

```bash
/home/gismar/.hermes/hermes-agent/venv/bin/python - <<'PY'
import yaml
for path in ['/home/gismar/.hermes/config.yaml','/home/gismar/.hermes/profiles/mara-codexrt/config.yaml']:
    data = yaml.safe_load(open(path, encoding='utf-8'))
    print(path)
    print('context.engine=', data['context']['engine'])
    print('compression.enabled=', data['compression']['enabled'])
    print('plugins.enabled has hermes-lcm=', 'hermes-lcm' in data['plugins']['enabled'])
    print('plugins.enabled has lossless-hermes=', 'lossless-hermes' in data['plugins']['enabled'])
    print('plugins.disabled has lossless-hermes=', 'lossless-hermes' in data['plugins']['disabled'])
PY
```

Result:

```text
/home/gismar/.hermes/config.yaml
context.engine= lcm
compression.enabled= True
plugins.enabled has hermes-lcm= True
plugins.enabled has lossless-hermes= False
plugins.disabled has lossless-hermes= True
/home/gismar/.hermes/profiles/mara-codexrt/config.yaml
context.engine= lcm
compression.enabled= True
plugins.enabled has hermes-lcm= True
plugins.enabled has lossless-hermes= False
plugins.disabled has lossless-hermes= True
```

LCM smoke test used a temporary Hermes home at `/tmp/hermes-lcm-smoke`, `context_length=300000`, and monkeypatched summarization to avoid a live model call.

Result:

```json
{
  "below_300k": true,
  "config": {
    "deferred_maintenance_enabled": true,
    "fresh_tail_count": 16,
    "leaf_chunk_tokens": 2000
  },
  "debt_kind": "raw_backlog",
  "debt_size_estimate": 14505,
  "last_compression_status": "compacted",
  "preflight_after_first_compress": true,
  "preflight_before_compress": true,
  "rough_tokens": 30777,
  "stored_messages": 35,
  "summary_nodes": 1
}
```

This proves `should_compress_preflight()` can request maintenance below the normal threshold and that LCM can create summaries/maintenance debt under 300k tokens.

## Still Needs Live Testing

- Restart the relevant Hermes gateway/proxy process from outside this routed Codex session.
- Run one real long session and confirm `lcm_status` reports the active `lcm` engine, stored messages, and summary/debt lifecycle.
- Confirm no production profile still enables `lossless-hermes`.

## Kanban

The local Hermes CLI could not find `t_1177f6c1` on the visible boards, so no Kanban claim/comment/complete action was taken.
Do not mark done until Hermes/Mara review confirms the branch and runtime config.
