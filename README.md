# Jenkins Log Analyzer

A lightweight FastAPI service that accepts Jenkins build logs, sends them to any OpenAI-compatible LLM gateway, and returns structured root cause analysis with fix suggestions. Results are persisted in SQLite for history queries.

## Features

- `POST /analyze` — synchronous analysis: submit a log, get back root cause + suggested fix + confidence
- `POST /analyze/stream` — same analysis over Server-Sent Events (SSE) as tokens stream in
- `GET /jobs/{job_name}/history` — query past analyses for a job, newest first
- `GET /health` — liveness check
- Works with any OpenAI-compatible gateway (haimaker.ai, LiteLLM, OpenAI, etc.)
- Handles model quirks: strips `<think>` reasoning blocks, normalises float confidence scores

## Quick Start

### With Docker

```bash
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  -e OPENAI_BASE_URL=https://api.haimaker.ai/v1 \
  -e OPENAI_API_KEY=your-key \
  -e MODEL_NAME=minimax/MiniMax-M2.7 \
  -e DB_PATH=/data/analyzer.db \
  ghcr.io/theatricshrink/jenkins-analyzer:latest
```

### With Docker Compose

Create a `.env` file (never commit this):

```env
OPENAI_BASE_URL=https://api.haimaker.ai/v1
OPENAI_API_KEY=your-key-here
MODEL_NAME=minimax/MiniMax-M2.7
DB_PATH=/data/analyzer.db
```

Then:

```bash
docker compose up -d
```

The service listens on port `8000`.

## Configuration

All settings are environment variables — no config files, no restarts needed beyond container recreation.

| Variable | Required | Description |
|---|---|---|
| `OPENAI_BASE_URL` | Yes | Base URL of your OpenAI-compatible gateway |
| `OPENAI_API_KEY` | Yes | API key for the gateway |
| `MODEL_NAME` | Yes | Model identifier (e.g. `minimax/MiniMax-M2.7`, `gpt-4o`, `claude-3-haiku`) |
| `DB_PATH` | No | SQLite file path (default: `/data/analyzer.db`) |

### Switching Gateways

To switch from haimaker.ai to a LiteLLM gateway in production, update three variables:

```env
OPENAI_BASE_URL=http://your-litellm-host:4000
OPENAI_API_KEY=your-litellm-key
MODEL_NAME=your-preferred-model
```

No code changes needed.

## API

### POST /analyze

Submit a Jenkins build log for analysis.

**Request:**
```json
{
  "log": "<full Jenkins console output>",
  "job_name": "my-pipeline",
  "build_number": 42
}
```

**Response:**
```json
{
  "id": 1,
  "job_name": "my-pipeline",
  "build_number": 42,
  "root_cause": "Gradle failed to resolve com.google.guava:guava:33.0.0-jre — no matching variant found",
  "suggested_fix": "Pin guava to 32.1.3-jre in build.gradle and run ./gradlew --refresh-dependencies",
  "confidence": "high",
  "created_at": "2026-05-30T12:34:56.123456+00:00"
}
```

### POST /analyze/stream

Same request body. Returns an SSE stream of `{"delta": "..."}` events while the model responds, followed by a final `{"done": true, "root_cause": "...", ...}` event.

### GET /jobs/{job_name}/history

Returns all past analyses for a job, newest first.

```bash
curl http://localhost:8000/jobs/my-pipeline/history
```

### GET /health

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## Jenkins Pipeline Integration

Call the analyzer from a Jenkinsfile or shared library function after a build failure:

```groovy
// In a Jenkins pipeline (requires jq on the agent)
stage('Analyze Failure') {
    when { expression { currentBuild.result == 'FAILURE' } }
    steps {
        script {
            def analysis = sh(
                returnStdout: true,
                script: """
                    jq -n --rawfile log build.log \
                      '{"log": \$log, "job_name": "${JOB_NAME}", "build_number": ${BUILD_NUMBER}}' \
                    | curl -sf -X POST http://your-analyzer-host:8000/analyze \
                        -H "Content-Type: application/json" --data @-
                """
            ).trim()
            def parsed = readJSON text: analysis
            echo "Root cause: ${parsed.root_cause}"
            echo "Suggested fix: ${parsed.suggested_fix}"
        }
    }
}
```

Or with plain `curl` in a shell step:

```bash
ANALYSIS=$(jq -n --rawfile log build.log \
  '{"log": $log, "job_name": "my-job", "build_number": 1}' \
  | curl -sf -X POST http://your-analyzer-host:8000/analyze \
      -H "Content-Type: application/json" --data @-)

echo "Root cause   : $(echo "$ANALYSIS" | jq -r .root_cause)"
echo "Suggested fix: $(echo "$ANALYSIS" | jq -r .suggested_fix)"
echo "Confidence   : $(echo "$ANALYSIS" | jq -r .confidence)"
```

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
pytest
```

## Project Layout

```
.
├── main.py               # FastAPI app — all endpoints and LLM logic
├── Dockerfile
├── requirements.txt      # Production dependencies
├── requirements-test.txt # Test dependencies
├── pytest.ini
└── tests/
    └── test_main.py
```

## Future: Autonomous Fix Agent

The `/analyze` endpoint returns suggestions only. A planned `/fix` endpoint will use LLM tool-use to take autonomous actions — git commits, PR creation, pipeline reruns — using the SQLite history as context. No architectural changes to the current service are required to support this extension.
