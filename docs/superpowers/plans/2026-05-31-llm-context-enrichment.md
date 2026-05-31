# LLM Context Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the LLM with Jenkins log anatomy, failure categories, job metadata, optional log trimming, and automatic DB cleanup.

**Architecture:** All changes are confined to `main.py` and `tests/test_main.py`. A `_parse_llm_json` helper centralises response parsing for both the sync and streaming endpoints. DB migration is handled in `init_db` via a try/except on `ALTER TABLE`. A background `asyncio` task runs cleanup every 24 hours inside the existing `lifespan` context manager.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest-asyncio, AsyncOpenAI client

---

## File Map

| File | Changes |
|---|---|
| `main.py` | System prompt, `_VALID_CATEGORIES`, `_parse_llm_json`, `call_llm` signature, `AnalyzeRequest`, `AnalysisResult`, `init_db`, `persist`, `job_history`, `analyze`, `analyze_stream`, `_run_cleanup`, `_cleanup_loop`, `lifespan` |
| `tests/test_main.py` | Update existing test payloads and assertions; add 5 new tests |
| `README.md` | Features, config table, API examples |

---

## Task 1: Add `failure_category` to parsing, schema, DB, and persistence

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Update test helper and existing payloads**

In `tests/test_main.py`, add `failure_category` to every mock payload and update `_fake_completion`:

```python
def _fake_completion(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(payload)
    return resp
```

Update `test_analyze_returns_structured_result` payload and add assertion:

```python
async def test_analyze_returns_structured_result(ac):
    payload = {
        "root_cause": "guava:33 not found",
        "suggested_fix": "pin to guava:32.1.3-jre",
        "confidence": "high",
        "failure_category": "dependency",
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_fake_completion(payload))
    mock_client.close = AsyncMock()
    with patch.object(app.state, "llm", mock_client, create=True):
        r = await ac.post("/analyze", json={"log": "fake log", "job_name": "ci-job", "build_number": 7})

    assert r.status_code == 200
    body = r.json()
    assert body["root_cause"] == "guava:33 not found"
    assert body["suggested_fix"] == "pin to guava:32.1.3-jre"
    assert body["confidence"] == "high"
    assert body["failure_category"] == "dependency"
    assert body["job_name"] == "ci-job"
    assert body["build_number"] == 7
    assert isinstance(body["id"], int)
    assert "created_at" in body
```

Update `test_analyze_persists_to_history` payload and assertion:

```python
async def test_analyze_persists_to_history(ac):
    payload = {
        "root_cause": "test fail",
        "suggested_fix": "fix test",
        "confidence": "medium",
        "failure_category": "test",
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_fake_completion(payload))
    mock_client.close = AsyncMock()
    with patch.object(app.state, "llm", mock_client, create=True):
        await ac.post("/analyze", json={"log": "log data", "job_name": "history-job", "build_number": 3})

    r = await ac.get("/jobs/history-job/history")
    assert r.status_code == 200
    history = r.json()
    assert len(history) >= 1
    assert history[0]["job_name"] == "history-job"
    assert history[0]["build_number"] == 3
    assert history[0]["root_cause"] == "test fail"
    assert history[0]["failure_category"] == "test"
```

- [ ] **Step 2: Add new tests for failure_category validation and DB migration**

Append to `tests/test_main.py`:

```python
async def test_invalid_failure_category_defaults_to_other(ac):
    payload = {
        "root_cause": "r",
        "suggested_fix": "f",
        "confidence": "high",
        "failure_category": "NONSENSE",
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_fake_completion(payload))
    mock_client.close = AsyncMock()
    with patch.object(app.state, "llm", mock_client, create=True):
        r = await ac.post("/analyze", json={"log": "log", "job_name": "job", "build_number": 1})
    assert r.status_code == 200
    assert r.json()["failure_category"] == "other"


async def test_init_db_migrates_existing_database():
    import tempfile
    from unittest.mock import patch as upatch
    import aiosqlite as _aiosqlite
    from main import init_db as _init_db

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        async with _aiosqlite.connect(tmp.name) as db:
            await db.execute("""
                CREATE TABLE analyses (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_name      TEXT NOT NULL,
                    build_number  INTEGER NOT NULL,
                    log_text      TEXT NOT NULL,
                    root_cause    TEXT NOT NULL,
                    suggested_fix TEXT NOT NULL,
                    confidence    TEXT NOT NULL,
                    created_at    TEXT NOT NULL
                )
            """)
            await db.commit()

        with upatch("main.DB_PATH", tmp.name):
            await _init_db()

        async with _aiosqlite.connect(tmp.name) as db:
            cursor = await db.execute("PRAGMA table_info(analyses)")
            columns = [row[1] for row in await cursor.fetchall()]

        assert "failure_category" in columns
    finally:
        import os as _os
        _os.unlink(tmp.name)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /root/jenkins-analyzer-repo && pip install -r requirements-test.txt -q && pytest -x -v
```

Expected: failures on `test_analyze_returns_structured_result` (no `failure_category` key in response), `test_invalid_failure_category_defaults_to_other`, `test_init_db_migrates_existing_database`.

- [ ] **Step 4: Replace `SYSTEM_PROMPT` in `main.py`**

Replace lines 15–19 with:

```python
SYSTEM_PROMPT = """\
You are a CI/CD expert analyzing Jenkins build logs.

Jenkins log structure:
- Stage headers look like: [Pipeline] stage("Build")
- Each step's output follows its [Pipeline] marker
- Errors typically appear in the final stages before the BUILD FAILED banner
- The log closes with EXIT CODE: <N> and BUILD FAILED or BUILD SUCCESS

Failure categories — pick exactly one:
  build          : compilation or packaging failure (Gradle, Maven, npm, make, etc.)
  test           : unit or integration test failures
  dependency     : unresolvable dependency or version conflict
  infrastructure : OOM kill, disk full, network timeout, agent unavailable
  pipeline       : Groovy/Jenkinsfile syntax error or Jenkins plugin failure
  other          : none of the above

Return ONLY valid JSON with these exact keys:
  root_cause       (string)  — specific cause of the failure
  suggested_fix    (string)  — concrete remediation step
  confidence       (one of: high, medium, low)
  failure_category (one of: build, test, dependency, infrastructure, pipeline, other)

Do not include any text outside the JSON object.\
"""

_VALID_CATEGORIES = frozenset({"build", "test", "dependency", "infrastructure", "pipeline", "other"})
```

- [ ] **Step 5: Add `_parse_llm_json` helper to `main.py`**

Insert after the `_VALID_CATEGORIES` line (before `async def init_db`):

```python
def _parse_llm_json(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if json_match:
        content = json_match.group(0)
    parsed = json.loads(content)  # raises json.JSONDecodeError on bad JSON
    _ = parsed["root_cause"], parsed["suggested_fix"], parsed["confidence"]  # raises KeyError if missing
    conf = str(parsed["confidence"]).lower()
    try:
        conf_float = float(conf)
        parsed["confidence"] = "high" if conf_float >= 0.7 else "medium" if conf_float >= 0.4 else "low"
    except ValueError:
        if conf not in ("high", "medium", "low"):
            parsed["confidence"] = "medium"
    cat = str(parsed.get("failure_category", "")).lower()
    parsed["failure_category"] = cat if cat in _VALID_CATEGORIES else "other"
    return parsed
```

- [ ] **Step 6: Update `call_llm` to use `_parse_llm_json`**

Replace the entire `call_llm` function body (lines 70–108):

```python
async def call_llm(client: AsyncOpenAI, log: str) -> dict:
    model = os.environ.get("MODEL_NAME", "minimax/MiniMax-M2.7")
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": log},
        ],
    )
    content = response.choices[0].message.content
    if content is None:
        raise HTTPException(status_code=422, detail="LLM returned no content")
    try:
        return _parse_llm_json(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {e}")
    except KeyError as e:
        raise HTTPException(status_code=422, detail=f"LLM response missing required key: {e}")
```

- [ ] **Step 7: Add `failure_category` to `AnalysisResult`**

```python
class AnalysisResult(BaseModel):
    id: int
    job_name: str
    build_number: int
    root_cause: str
    suggested_fix: str
    confidence: str
    failure_category: str
    created_at: str
```

- [ ] **Step 8: Update `analyze` endpoint to include `failure_category` in its return**

```python
@app.post("/analyze", response_model=AnalysisResult)
async def analyze(req: AnalyzeRequest, request: Request) -> AnalysisResult:
    result = await call_llm(request.app.state.llm, req.log)
    row_id, now = await persist(req, result)
    return AnalysisResult(
        id=row_id,
        job_name=req.job_name,
        build_number=req.build_number,
        root_cause=result["root_cause"],
        suggested_fix=result["suggested_fix"],
        confidence=result["confidence"],
        failure_category=result["failure_category"],
        created_at=now,
    )
```

- [ ] **Step 9: Update `analyze_stream` to use `_parse_llm_json`**

Replace the `event_generator` try/except block:

```python
        try:
            result = _parse_llm_json(collected)
            await persist(req, result)
            yield f"data: {json.dumps({'done': True, **result})}\n\n"
        except (json.JSONDecodeError, KeyError, TypeError):
            yield f"data: {json.dumps({'error': 'model returned invalid JSON'})}\n\n"
```

- [ ] **Step 10: Update `init_db` to add the `failure_category` column and migrate existing DBs**

Replace the full `init_db` function:

```python
async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name         TEXT    NOT NULL,
                build_number     INTEGER NOT NULL,
                log_text         TEXT    NOT NULL,
                root_cause       TEXT    NOT NULL,
                suggested_fix    TEXT    NOT NULL,
                confidence       TEXT    NOT NULL,
                failure_category TEXT    NOT NULL DEFAULT 'other',
                created_at       TEXT    NOT NULL
            )
        """)
        try:
            await db.execute(
                "ALTER TABLE analyses ADD COLUMN failure_category TEXT NOT NULL DEFAULT 'other'"
            )
        except aiosqlite.OperationalError:
            pass  # column already exists in pre-existing databases
        await db.commit()
```

- [ ] **Step 11: Update `persist` to store `failure_category`**

```python
async def persist(req: AnalyzeRequest, result: dict) -> tuple[int, str]:
    await init_db()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO analyses
               (job_name, build_number, log_text, root_cause, suggested_fix, confidence, failure_category, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (req.job_name, req.build_number, req.log,
             result["root_cause"], result["suggested_fix"], result["confidence"],
             result["failure_category"], now),
        )
        await db.commit()
        return cursor.lastrowid, now
```

- [ ] **Step 12: Update `job_history` to return `failure_category`**

```python
    return [
        AnalysisResult(
            id=row["id"],
            job_name=row["job_name"],
            build_number=row["build_number"],
            root_cause=row["root_cause"],
            suggested_fix=row["suggested_fix"],
            confidence=row["confidence"],
            failure_category=row["failure_category"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
```

- [ ] **Step 13: Run tests to verify they pass**

```bash
pytest -x -v
```

Expected: all existing tests pass, new tests pass.

- [ ] **Step 14: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: add failure_category to LLM parsing, schema, and DB"
```

---

## Task 2: Structured user message — job metadata and optional log trimming

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_main.py`:

```python
async def test_user_message_includes_job_metadata(ac):
    payload = {
        "root_cause": "r", "suggested_fix": "f",
        "confidence": "high", "failure_category": "build",
    }
    captured = []

    async def mock_create(*args, **kwargs):
        captured.extend(kwargs.get("messages", []))
        return _fake_completion(payload)

    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    mock_client.close = AsyncMock()

    with patch.object(app.state, "llm", mock_client, create=True):
        r = await ac.post("/analyze", json={
            "log": "some log output",
            "job_name": "payments-service",
            "build_number": 42,
        })

    assert r.status_code == 200
    user_msg = captured[1]["content"]
    assert "Job: payments-service | Build: #42" in user_msg
    assert "some log output" in user_msg


async def test_tail_lines_trims_log(ac):
    payload = {
        "root_cause": "r", "suggested_fix": "f",
        "confidence": "high", "failure_category": "test",
    }
    captured = []

    async def mock_create(*args, **kwargs):
        captured.extend(kwargs.get("messages", []))
        return _fake_completion(payload)

    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    mock_client.close = AsyncMock()

    log = "\n".join(str(i) for i in range(20))  # lines "0" through "19"

    with patch.object(app.state, "llm", mock_client, create=True):
        r = await ac.post("/analyze", json={
            "log": log,
            "job_name": "trim-job",
            "build_number": 5,
            "tail_lines": 5,
        })

    assert r.status_code == 200
    user_msg = captured[1]["content"]
    # Last 5 lines: "15\n16\n17\n18\n19"
    assert "15\n16\n17\n18\n19" in user_msg
    assert "0\n1" not in user_msg
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest -x -v -k "test_user_message_includes_job_metadata or test_tail_lines_trims_log"
```

Expected: both tests fail because the user message is currently the raw log with no metadata.

- [ ] **Step 3: Add `tail_lines` to `AnalyzeRequest`**

```python
class AnalyzeRequest(BaseModel):
    log: str
    job_name: str
    build_number: int
    tail_lines: int | None = None
```

- [ ] **Step 4: Update `call_llm` signature and user message building**

```python
async def call_llm(
    client: AsyncOpenAI,
    log: str,
    job_name: str,
    build_number: int,
    tail_lines: int | None = None,
) -> dict:
    model = os.environ.get("MODEL_NAME", "minimax/MiniMax-M2.7")
    log_text = "\n".join(log.splitlines()[-tail_lines:]) if tail_lines is not None else log
    user_message = f"Job: {job_name} | Build: #{build_number}\n\n{log_text}"
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    content = response.choices[0].message.content
    if content is None:
        raise HTTPException(status_code=422, detail="LLM returned no content")
    try:
        return _parse_llm_json(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {e}")
    except KeyError as e:
        raise HTTPException(status_code=422, detail=f"LLM response missing required key: {e}")
```

- [ ] **Step 5: Update `analyze` endpoint to pass metadata through**

```python
@app.post("/analyze", response_model=AnalysisResult)
async def analyze(req: AnalyzeRequest, request: Request) -> AnalysisResult:
    result = await call_llm(
        request.app.state.llm, req.log, req.job_name, req.build_number, req.tail_lines
    )
    row_id, now = await persist(req, result)
    return AnalysisResult(
        id=row_id,
        job_name=req.job_name,
        build_number=req.build_number,
        root_cause=result["root_cause"],
        suggested_fix=result["suggested_fix"],
        confidence=result["confidence"],
        failure_category=result["failure_category"],
        created_at=now,
    )
```

- [ ] **Step 6: Update `analyze_stream` to build structured user message**

Replace the body of `analyze_stream` (before `event_generator`):

```python
@app.post("/analyze/stream")
async def analyze_stream(req: AnalyzeRequest, request: Request):
    client = request.app.state.llm
    model = os.environ.get("MODEL_NAME", "minimax/MiniMax-M2.7")
    log_text = "\n".join(req.log.splitlines()[-req.tail_lines:]) if req.tail_lines is not None else req.log
    user_message = f"Job: {req.job_name} | Build: #{req.build_number}\n\n{log_text}"

    async def event_generator():
        collected = ""
        stream = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or "" if chunk.choices else ""
            if delta:
                collected += delta
                yield f"data: {json.dumps({'delta': delta})}\n\n"

        try:
            result = _parse_llm_json(collected)
            await persist(req, result)
            yield f"data: {json.dumps({'done': True, **result})}\n\n"
        except (json.JSONDecodeError, KeyError, TypeError):
            yield f"data: {json.dumps({'error': 'model returned invalid JSON'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 7: Run all tests**

```bash
pytest -x -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: pass job metadata and optional tail_lines to LLM"
```

---

## Task 3: Background cleanup task

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_main.py`. Also add `import aiosqlite as _aiosqlite` at the top of the file (if not already present from Task 1).

```python
async def test_cleanup_deletes_old_records():
    import aiosqlite as _aiosqlite
    from main import _run_cleanup

    db_path = os.environ["DB_PATH"]
    async with _aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO analyses
               (job_name, build_number, log_text, root_cause, suggested_fix,
                confidence, failure_category, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("old-job", 99, "log", "cause", "fix", "high", "build",
             "2020-01-01T00:00:00+00:00"),
        )
        await db.commit()

    await _run_cleanup()

    async with _aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM analyses WHERE job_name = ?", ("old-job",)
        )
        (count,) = await cursor.fetchone()

    assert count == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest -x -v -k "test_cleanup_deletes_old_records"
```

Expected: `ImportError: cannot import name '_run_cleanup' from 'main'`.

- [ ] **Step 3: Add imports to `main.py`**

Update the import block at the top of `main.py`:

```python
import asyncio
import contextlib
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel
```

- [ ] **Step 4: Add `_run_cleanup` and `_cleanup_loop` to `main.py`**

Insert after the `lifespan` function's closing line (before `app = FastAPI(...)`):

```python
async def _run_cleanup() -> None:
    retention_days = int(os.environ.get("RETENTION_DAYS", "90"))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM analyses WHERE created_at < ?", (cutoff,))
        await db.commit()


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(86400)  # wait 24 h before first and every subsequent run
        try:
            await _run_cleanup()
        except Exception:
            pass
```

- [ ] **Step 5: Update `lifespan` to start and cancel the cleanup task**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.llm = AsyncOpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )
    app.state.cleanup_task = asyncio.create_task(_cleanup_loop())
    yield
    app.state.cleanup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await app.state.cleanup_task
    await app.state.llm.close()
```

- [ ] **Step 6: Run all tests**

```bash
pytest -x -v
```

Expected: all tests pass including `test_cleanup_deletes_old_records`.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: add background DB cleanup task with RETENTION_DAYS config"
```

---

## Task 4: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update Features section**

Replace the existing features bullet list with:

```markdown
## Features

- `POST /analyze` — synchronous analysis: submit a log, get back root cause + suggested fix + confidence + failure category
- `POST /analyze/stream` — same analysis over Server-Sent Events (SSE) as tokens stream in
- `GET /jobs/{job_name}/history` — query past analyses for a job, newest first
- `GET /health` — liveness check
- Works with any OpenAI-compatible gateway (haimaker.ai, LiteLLM, OpenAI, etc.)
- Classifies failures into one of six categories: `build`, `test`, `dependency`, `infrastructure`, `pipeline`, `other`
- Optional `tail_lines` parameter to send only the last N lines of a large log to the LLM
- Automatic DB cleanup: records older than `RETENTION_DAYS` days are deleted daily in the background
- Handles model quirks: strips `<think>` reasoning blocks, normalises float confidence scores
```

- [ ] **Step 2: Update Configuration table**

Replace the configuration table with:

```markdown
| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_BASE_URL` | Yes | — | Base URL of your OpenAI-compatible gateway |
| `OPENAI_API_KEY` | Yes | — | API key for the gateway |
| `MODEL_NAME` | Yes | — | Model identifier (e.g. `minimax/MiniMax-M2.7`, `gpt-4o`) |
| `DB_PATH` | No | `/data/analyzer.db` | SQLite file path |
| `RETENTION_DAYS` | No | `90` | Days to retain analysis records before automatic deletion |
```

- [ ] **Step 3: Update POST /analyze API section**

Replace the request and response examples:

```markdown
### POST /analyze

Submit a Jenkins build log for analysis.

**Request:**
```json
{
  "log": "<full Jenkins console output>",
  "job_name": "my-pipeline",
  "build_number": 42,
  "tail_lines": 200
}
```

`tail_lines` is optional. When set, only the last N lines of `log` are sent to the LLM. Omit it (or pass `null`) to send the full log.

**Response:**
```json
{
  "id": 1,
  "job_name": "my-pipeline",
  "build_number": 42,
  "root_cause": "Gradle failed to resolve com.google.guava:guava:33.0.0-jre — no matching variant found",
  "suggested_fix": "Pin guava to 32.1.3-jre in build.gradle and run ./gradlew --refresh-dependencies",
  "confidence": "high",
  "failure_category": "dependency",
  "created_at": "2026-05-30T12:34:56.123456+00:00"
}
```

`failure_category` is one of: `build`, `test`, `dependency`, `infrastructure`, `pipeline`, `other`.
```

- [ ] **Step 4: Update POST /analyze/stream API section**

```markdown
### POST /analyze/stream

Same request body as `/analyze` (including optional `tail_lines`). Returns an SSE stream of `{"delta": "..."}` events while the model responds, followed by a final event that includes all result fields:

```json
{"done": true, "root_cause": "...", "suggested_fix": "...", "confidence": "high", "failure_category": "dependency"}
```
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: update README for failure_category, tail_lines, and RETENTION_DAYS"
```

---

## Self-Review Checklist

- [x] Spec section 1 (system prompt) — covered in Task 1 Steps 4–6
- [x] Spec section 2 (tail_lines, structured user message) — covered in Task 2
- [x] Spec section 3 (failure_category in response, DB migration, persist, history) — covered in Task 1 Steps 7–12
- [x] Spec section 4 (background cleanup, RETENTION_DAYS) — covered in Task 3
- [x] Spec README section — covered in Task 4
- [x] No placeholders or TBDs
- [x] Type consistency: `tail_lines: int | None`, `failure_category: str`, `_run_cleanup` exported for tests
- [x] Streaming endpoint updated in both Task 1 (parser) and Task 2 (user message)
- [x] `asyncio` and `contextlib` imports added before use
- [x] `timedelta` added to datetime import
