import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

DB_PATH = os.getenv("DB_PATH", "/data/analyzer.db")

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


def _parse_llm_json(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if json_match:
        content = json_match.group(0)
    parsed = json.loads(content)  # raises json.JSONDecodeError on bad JSON
    for key in ("root_cause", "suggested_fix", "confidence"):
        if key not in parsed:
            raise KeyError(key)
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
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.llm = AsyncOpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )
    yield
    await app.state.llm.close()


app = FastAPI(title="Jenkins Log Analyzer", lifespan=lifespan)


class AnalyzeRequest(BaseModel):
    log: str
    job_name: str
    build_number: int
    tail_lines: int | None = Field(default=None, gt=0)


class AnalysisResult(BaseModel):
    id: int
    job_name: str
    build_number: int
    root_cause: str
    suggested_fix: str
    confidence: str
    failure_category: str
    created_at: str


def _build_user_message(log: str, job_name: str, build_number: int, tail_lines: int | None) -> str:
    log_text = "\n".join(log.splitlines()[-tail_lines:]) if tail_lines is not None else log
    return f"Job: {job_name} | Build: #{build_number}\n\n{log_text}"


async def call_llm(
    client: AsyncOpenAI,
    log: str,
    job_name: str,
    build_number: int,
    tail_lines: int | None = None,
) -> dict:
    model = os.environ.get("MODEL_NAME", "minimax/MiniMax-M2.7")
    user_message = _build_user_message(log, job_name, build_number, tail_lines)
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


@app.get("/health")
async def health():
    return {"status": "ok"}


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


@app.post("/analyze/stream")
async def analyze_stream(req: AnalyzeRequest, request: Request):
    client = request.app.state.llm
    model = os.environ.get("MODEL_NAME", "minimax/MiniMax-M2.7")
    user_message = _build_user_message(req.log, req.job_name, req.build_number, req.tail_lines)

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


@app.get("/jobs/{job_name}/history", response_model=list[AnalysisResult])
async def job_history(job_name: str) -> list[AnalysisResult]:
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM analyses WHERE job_name = ? ORDER BY id DESC",
            (job_name,),
        )
        rows = await cursor.fetchall()
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
