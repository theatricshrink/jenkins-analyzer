# LLM Context Enrichment Design

**Date:** 2026-05-31
**Status:** Approved

## Problem

The current system prompt gives the LLM minimal context — it knows to return a JSON object but has no guidance on Jenkins log structure, what failure categories exist, or which job/build it is analyzing. `job_name` and `build_number` from the request are never forwarded to the LLM. The SQLite database grows without bound.

## Goals

1. Give the LLM richer upfront context: Jenkins log anatomy and an enumerated set of failure categories.
2. Include `job_name` and `build_number` in every LLM call.
3. Add `failure_category` to the response schema and DB.
4. Allow callers to optionally trim large logs via a `tail_lines` request parameter.
5. Automatically purge old records via a background task.

## Design

### 1. System Prompt

Replace the one-liner with an expanded prompt (~25 lines) that covers:

- **Role:** CI/CD expert analyzing Jenkins build logs.
- **Log anatomy:** Jenkins logs contain `[Pipeline]` stage markers, timestamps, per-step output, and close with `EXIT CODE` and `BUILD FAILED` / `BUILD SUCCESS` banners. Errors typically appear in the final stages.
- **Failure categories** (model must pick exactly one):
  - `build` — compilation or packaging failure (Gradle, Maven, npm, etc.)
  - `test` — unit or integration test failures
  - `dependency` — unresolvable dependency or version conflict
  - `infrastructure` — OOM, disk full, network timeout, agent unavailable
  - `pipeline` — Groovy/Jenkinsfile syntax or plugin error
  - `other` — none of the above
- **Output schema:** return ONLY valid JSON with keys `root_cause` (string), `suggested_fix` (string), `confidence` (`high` | `medium` | `low`), `failure_category` (one of the six above). No text outside the JSON object.

### 2. Request Changes

Add one optional field to `AnalyzeRequest`:

```python
tail_lines: int | None = None  # if set, only the last N lines of the log are sent
```

`call_llm` signature changes to accept `job_name: str`, `build_number: int`, and `log: str`. It builds the user message as:

```
Job: <job_name> | Build: #<build_number>

<log>          # or last tail_lines lines if tail_lines is set
```

Both `/analyze` and `/analyze/stream` pass `job_name`, `build_number`, and (optionally trimmed) log through to `call_llm`.

### 3. Response & Persistence

- `AnalysisResult` gains `failure_category: str`.
- `analyses` table gains a `failure_category TEXT NOT NULL DEFAULT 'other'` column, added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `init_db()` so existing databases are not broken.
- `persist()` stores `failure_category`.
- `call_llm` parser validates `failure_category` is present and is one of the six allowed values; defaults to `"other"` if the model returns an unrecognised value.
- Both endpoints return `failure_category` in their responses. The streaming endpoint includes it in the final `done` event.

### 4. Background Cleanup Task

A `asyncio.Task` is started in the `lifespan` context manager:

- On startup: create the task, store it on `app.state.cleanup_task`.
- Loop: delete rows where `created_at < now - RETENTION_DAYS days`, then `asyncio.sleep(86400)` (24 hours).
- On shutdown: cancel the task and await it (suppressing `CancelledError`).

`RETENTION_DAYS` is a new env var defaulting to `90`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `RETENTION_DAYS` | `90` | Days to retain analysis records |

`tail_lines` is per-request, not global config.

## Data Flow

```
POST /analyze
  │
  ├─ trim log if tail_lines set
  ├─ call_llm(job_name, build_number, log)
  │    ├─ build structured user message
  │    ├─ send to LLM
  │    └─ parse + validate JSON (incl. failure_category)
  ├─ persist(req, result)          ← stores failure_category
  └─ return AnalysisResult         ← includes failure_category
```

## Testing

- Existing tests continue to pass with updated mock payloads that include `failure_category`.
- New test: `tail_lines` trims the log correctly before LLM call.
- New test: unrecognised `failure_category` from model defaults to `"other"`.
- New test: existing DB without `failure_category` column is migrated cleanly on startup.

## README Updates

Update `README.md` to reflect all changes:

- **Configuration table:** add `RETENTION_DAYS` env var.
- **API — POST /analyze:** update request and response examples to include `tail_lines` (optional) and `failure_category`.
- **API — POST /analyze/stream:** note `failure_category` appears in the final `done` event.
- **Features list:** add entries for log trimming, failure categorization, and automatic DB cleanup.
- **Development section:** document the new env var.

## Out of Scope

- No new API endpoints.
- No changes to the streaming SSE protocol beyond adding `failure_category` to the final event.
- No UI or dashboard.
