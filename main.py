import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel

DB_PATH = os.getenv("DB_PATH", "/data/analyzer.db")

SYSTEM_PROMPT = (
    "You are a CI/CD expert. Analyze the Jenkins build log and return ONLY valid JSON "
    "with keys: root_cause (string), suggested_fix (string), confidence "
    "(one of: high, medium, low). Do not include any text outside the JSON object."
)



async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name      TEXT    NOT NULL,
                build_number  INTEGER NOT NULL,
                log_text      TEXT    NOT NULL,
                root_cause    TEXT    NOT NULL,
                suggested_fix TEXT    NOT NULL,
                confidence    TEXT    NOT NULL,
                created_at    TEXT    NOT NULL
            )
        """)
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


class AnalysisResult(BaseModel):
    id: int
    job_name: str
    build_number: int
    root_cause: str
    suggested_fix: str
    confidence: str
    created_at: str


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
    # Strip <think>...</think> reasoning blocks emitted by some models
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    # Extract the first JSON object from the response
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if json_match:
        content = json_match.group(0)
    try:
        parsed = json.loads(content)
        _ = parsed["root_cause"], parsed["suggested_fix"], parsed["confidence"]
        # Normalize confidence to high/medium/low if the model returned a float string
        conf = str(parsed["confidence"]).lower()
        try:
            conf_float = float(conf)
            if conf_float >= 0.7:
                parsed["confidence"] = "high"
            elif conf_float >= 0.4:
                parsed["confidence"] = "medium"
            else:
                parsed["confidence"] = "low"
        except ValueError:
            if conf not in ("high", "medium", "low"):
                parsed["confidence"] = "medium"
        return parsed
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
               (job_name, build_number, log_text, root_cause, suggested_fix, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (req.job_name, req.build_number, req.log,
             result["root_cause"], result["suggested_fix"], result["confidence"], now),
        )
        await db.commit()
        return cursor.lastrowid, now


@app.get("/health")
async def health():
    return {"status": "ok"}


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
        created_at=now,
    )


@app.post("/analyze/stream")
async def analyze_stream(req: AnalyzeRequest, request: Request):
    client = request.app.state.llm
    model = os.environ.get("MODEL_NAME", "minimax/MiniMax-M2.7")

    async def event_generator():
        collected = ""
        stream = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": req.log},
            ],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or "" if chunk.choices else ""
            if delta:
                collected += delta
                yield f"data: {json.dumps({'delta': delta})}\n\n"

        try:
            result = json.loads(collected)
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
            created_at=row["created_at"],
        )
        for row in rows
    ]
