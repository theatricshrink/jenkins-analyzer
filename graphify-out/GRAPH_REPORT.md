# Graph Report - .  (2026-06-07)

## Corpus Check
- Corpus is ~6,950 words - fits in a single context window. You may not need a graph.

## Summary
- 58 nodes · 126 edges · 6 communities (5 shown, 1 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 10 edges (avg confidence: 0.84)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Analyze API & Data Models|Analyze API & Data Models]]
- [[_COMMUNITY_LLM Prompting & Parsing|LLM Prompting & Parsing]]
- [[_COMMUNITY_Test Suite & Analytics|Test Suite & Analytics]]
- [[_COMMUNITY_Background Cleanup & Lifecycle|Background Cleanup & Lifecycle]]
- [[_COMMUNITY_Database & Job History|Database & Job History]]
- [[_COMMUNITY_Claude Code Config|Claude Code Config]]

## God Nodes (most connected - your core abstractions)
1. `query_analytics()` - 14 edges
2. `analyze()` - 10 edges
3. `init_db()` - 9 edges
4. `analyze_stream()` - 9 edges
5. `call_llm()` - 8 edges
6. `job_history()` - 7 edges
7. `_parse_llm_json` - 7 edges
8. `analyses SQLite Table` - 7 edges
9. `lifespan()` - 6 edges
10. `persist()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `Natural Language Query over SQLite Analytics` --rationale_for--> `query_analytics()`  [INFERRED]
  docs/superpowers/specs/2026-05-31-llm-context-enrichment-design.md → main.py
- `Background DB Cleanup Task` --rationale_for--> `_run_cleanup`  [INFERRED]
  docs/superpowers/specs/2026-05-31-llm-context-enrichment-design.md → main.py
- `tail_lines Log Trimming` --rationale_for--> `_build_user_message`  [INFERRED]
  docs/superpowers/specs/2026-05-31-llm-context-enrichment-design.md → main.py
- `Autonomous Fix Agent (planned /fix endpoint)` --references--> `analyses SQLite Table`  [INFERRED]
  README.md → main.py
- `LLM Context Enrichment Implementation Plan` --references--> `init_db()`  [EXTRACTED]
  docs/superpowers/plans/2026-05-31-llm-context-enrichment.md → main.py

## Import Cycles
- 1-file cycle: `main.py -> main.py`

## Hyperedges (group relationships)
- **POST /analyze request processing pipeline** — main_analyzerequest, main_call_llm, main__build_user_message, main__parse_llm_json, main_persist, main_analysisresult [EXTRACTED 0.95]
- **Database lifecycle management (init, persist, cleanup, query)** — main_init_db, main_persist, main__run_cleanup, main__cleanup_loop, main_lifespan, main_analyses_table [EXTRACTED 0.95]
- **POST /query natural-language-to-SQL analytics flow** — main_queryrequest, main_query_system_prompt, main_query_analytics, main_queryresponse, main_analyses_table [EXTRACTED 0.95]

## Communities (6 total, 1 thin omitted)

### Community 0 - "Analyze API & Data Models"
Cohesion: 0.32
Nodes (12): BaseModel, AnalysisResult, analyze(), analyze_stream(), AnalyzeRequest, _build_user_message(), health(), persist() (+4 more)

### Community 1 - "LLM Prompting & Parsing"
Cohesion: 0.19
Nodes (13): _build_user_message, _parse_llm_json, _VALID_CATEGORIES, call_llm(), Failure Category Taxonomy, _parse_llm_json(), SYSTEM_PROMPT, LLM Context Enrichment Implementation Plan (+5 more)

### Community 2 - "Test Suite & Analytics"
Cohesion: 0.30
Nodes (9): query_analytics(), _fake_completion(), _fake_completion_raw(), test_analyze_returns_structured_result(), test_invalid_failure_category_defaults_to_other(), test_query_handles_sql_error(), test_query_rejects_non_select(), test_query_returns_results() (+1 more)

### Community 3 - "Background Cleanup & Lifecycle"
Cohesion: 0.25
Nodes (9): AsyncOpenAI, FastAPI, _cleanup_loop, _run_cleanup, _cleanup_loop(), lifespan(), _run_cleanup(), Background DB Cleanup Task (+1 more)

### Community 4 - "Database & Job History"
Cohesion: 0.29
Nodes (8): analyses SQLite Table, init_db(), job_history(), QUERY_SYSTEM_PROMPT, Autonomous Fix Agent (planned /fix endpoint), test_analyze_persists_to_history(), test_history_empty_for_unknown_job(), test_init_db_migrates_existing_database()

## Knowledge Gaps
- **3 isolated node(s):** `allow`, `_VALID_CATEGORIES`, `Autonomous Fix Agent (planned /fix endpoint)`
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `query_analytics()` connect `Test Suite & Analytics` to `Analyze API & Data Models`, `LLM Prompting & Parsing`, `Database & Job History`?**
  _High betweenness centrality (0.163) - this node is a cross-community bridge._
- **Why does `_parse_llm_json` connect `LLM Prompting & Parsing` to `Analyze API & Data Models`, `Test Suite & Analytics`?**
  _High betweenness centrality (0.080) - this node is a cross-community bridge._
- **Why does `init_db()` connect `Database & Job History` to `Analyze API & Data Models`, `LLM Prompting & Parsing`, `Test Suite & Analytics`, `Background Cleanup & Lifecycle`?**
  _High betweenness centrality (0.074) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `query_analytics()` (e.g. with `_parse_llm_json` and `Natural Language Query over SQLite Analytics`) actually correct?**
  _`query_analytics()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `allow`, `_VALID_CATEGORIES`, `Autonomous Fix Agent (planned /fix endpoint)` to the rest of the system?**
  _3 weakly-connected nodes found - possible documentation gaps or missing edges._