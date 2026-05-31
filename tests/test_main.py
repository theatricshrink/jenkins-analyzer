import atexit
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Must be set before importing main so the DB lands in a temp file
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
atexit.register(lambda: os.unlink(_tmp.name) if os.path.exists(_tmp.name) else None)
os.environ.setdefault("OPENAI_BASE_URL", "https://api.test.local/v1")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("MODEL_NAME", "test-model")
os.environ["DB_PATH"] = _tmp.name

from main import app  # noqa: E402


def _fake_completion(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(payload)
    return resp


@pytest.fixture
async def ac():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_health(ac):
    r = await ac.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


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


async def test_history_empty_for_unknown_job(ac):
    r = await ac.get("/jobs/nonexistent-job-xyz/history")
    assert r.status_code == 200
    assert r.json() == []


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
