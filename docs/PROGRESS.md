# Progress

## Stage 0: Design, schemas, state, DB models

### Approved plan
1. **Git setup on `main`:** `git init -b main`; `.gitignore` first (exact block from CLAUDE.md); commits `chore: add .gitignore…`, `docs: add CLAUDE.md…`; branch `stage/0-design`.
2. **Scaffolding:** `backend/requirements.txt` (Stage 0 deps only, pinned), `backend/.env.example` (placeholders), package `__init__.py` files, `src/config.py` (env loading, model names, `MAX_ITERATIONS=3`), `pytest.ini`, venv at `backend\venv`.
3. **`docs/DESIGN.md`:** scope, Mermaid agent graph, forced-exit semantics, system architecture, auth decision, `core/` vs `api/` boundary, data model, streaming.
4. **`core/schemas.py`:** `AnalystOutput`, `CriticFeedback`, `AuditorVerdict` (extra fields forbidden), `MarketDataToolInput` (normalize to uppercase, reject empty / exchange suffix, regex `^[A-Z]{1,5}(-[A-Z])?$`).
5. **`core/state.py`:** `GraphState` as a Pydantic model with exactly the spec fields; `max_iterations >= 1`.
6. **DB:** `db/models.py` (users, research_runs with UUID ids, run_events; string enums; user-scoped query helpers), `db/session.py` (engine from `DATABASE_URL`, SQLite FK pragma, `init_db`, `get_db`).
7. **Tests:** `test_schemas.py`, `test_db_models.py` (incl. cross-user read protection).
8. **Docs:** README, MIT LICENSE, DECISIONS.md, PROGRESS.md, local DEVLOG.md.
9. **Git:** commits per unit on `stage/0-design`, `git merge --no-ff` into `main`, tag `stage-0-design`, secret scan, `git ls-files` check, private GitHub repo via `gh`, push `main` + tags.

### What was built
| File | Purpose |
|---|---|
| `.gitignore` | Secrets, venv, DBs, vector store, PDFs, study notes |
| `backend/requirements.txt` | pydantic 2.14, SQLAlchemy 2.1, python-dotenv, pytest |
| `backend/.env.example` | Placeholder env vars |
| `backend/pytest.ini` | `pythonpath = .` so tests import `src.*` |
| `backend/src/config.py` | Env loading, model names, `MAX_ITERATIONS` |
| `backend/src/core/schemas.py` | Agent output + tool input schemas |
| `backend/src/core/state.py` | `GraphState` |
| `backend/src/db/models.py` | ORM models + `get_runs_for_user`, `get_run_for_user` |
| `backend/src/db/session.py` | `make_engine`, `SessionLocal`, `init_db`, `get_db` |
| `backend/tests/test_schemas.py` | 28 schema/state tests |
| `backend/tests/test_db_models.py` | 8 DB tests |
| `docs/DESIGN.md`, `docs/DECISIONS.md`, `README.md`, `LICENSE` | Docs |

### How it was tested
- `cd backend; pytest -q` → **36 passed**.
- `init_db()` against the default URL created `backend/app.db` with tables `users`, `research_runs`, `run_events`; `git status --ignored` shows it as ignored.

### Next
Stage 1: yfinance wrapper in `src/tools/market_data.py` returning raw Python objects (no LLM).

## Stage 1: Data ingestion (yfinance, no LLM)

### Approved plan
1. **`src/tools/market_data.py`:** `fetch_market_data(ticker, ticker_factory=yf.Ticker) -> dict`. Validates via `MarketDataToolInput` before any network call; curated `INFO_FIELDS` mapping into `profile` / `price` / `valuation` / `financials`; `annual_financials` (up to 4 fiscal years, newest first); 1-year weekly `price_history`; `_clean()` makes everything JSON-safe. Exceptions: `MarketDataError` → `InvalidTickerError`, `TickerNotFoundError`, `DataSourceError`. CLI: `python -m src.tools.market_data AAPL`.
2. **`tests/test_market_data.py`:** offline tests with a `FakeTicker`, plus one `@pytest.mark.live` test (skipped by default).
3. **Config:** `yfinance` pinned in `requirements.txt`; `live` marker and `-m "not live"` default in `pytest.ini`.
4. **Docs:** README status + commands, DECISIONS D12-D16, PROGRESS, local DEVLOG.
5. **Git:** branch `stage/1-ingestion`; commits `chore(deps)…`, `feat(tools)…`, `docs…`; merge `--no-ff`, tag `stage-1-ingestion`, secret scan, push.
6. **Out of scope:** the `fetch_data` graph node (Stage 3) and retry/backoff (Stage 8).

### What was built
| File | Purpose |
|---|---|
| `backend/src/tools/market_data.py` | yfinance wrapper, typed exceptions, JSON-safe output, CLI |
| `backend/tests/test_market_data.py` | 15 offline tests + 1 live test |
| `backend/requirements.txt` | + yfinance 1.7.0, pandas 3.0.6, numpy 2.5.3 |
| `backend/pytest.ini` | `live` marker, offline by default |
| `README.md`, `docs/DECISIONS.md` (D12-D17) | Docs |

Changes from the plan (small): `annual_financials` uses `fiscal_year_end` (ISO date) instead of `year`, because fiscal years don't match calendar years (Apple's ends in September). The dividend yield field is named `dividend_yield_pct` (D17).

### How it was tested
- `pytest -q` → **51 passed, 1 deselected** (36 Stage 0 + 15 new).
- `pytest -m live` → real AAPL fetch **passed**.
- CLI: `AAPL` → full JSON, exit 0; `brk-b` → normalized to `BRK-B`, Berkshire data; `ZZZZQ` → `TickerNotFoundError`, exit 1; `RELIANCE.NS` and `ZZZZZZ` → `InvalidTickerError` before any network call, exit 1.

### Next
Stage 2: single Groq call with a Pydantic tool schema (`MarketDataToolInput`), rejecting malformed tool arguments. Needs `GROQ_API_KEY`.

## Stage 2: Tool calling + structured extraction (Groq)

### Approved plan
1. **`src/clients/groq_client.py`:** `GroqClient.chat()` with SDK retries off and our own exponential backoff on 429/5xx/connection/timeout (honours `retry-after`, capped at 30s). No retry on 400/401/404. `tool_use_failed` becomes `MalformedToolCallError`. A missing key raises `LLMConfigError`.
2. **`src/core/structured_output.py`:** `pydantic_to_tool()` and `call_with_schema()`. The call is forced with `tool_choice`, the arguments are validated with Pydantic, and there is one repair attempt with the error shown to the model; after that it raises `ToolCallError`.
3. **`src/core/prompts.py`:** `TICKER_EXTRACTION_PROMPT`.
4. **`src/core/tool_calling.py`:** `extract_ticker()` and `research_request_to_market_data()` (LLM, then yfinance), plus a CLI.
5. **Tests:** `test_groq_client.py`, `test_tool_calling.py` (offline fakes) and live tests.
6. **Docs, git:** DECISIONS D18-D22, branch `stage/2-tool-calling`, tag `stage-2-tool-calling`.

**Change made during the build (approved by the owner):** Groq no longer serves Llama chat models, so the models are now `openai/gpt-oss-20b` (tool calling) and `openai/gpt-oss-120b` (reasoning). CLAUDE.md, README and config were updated, and D23 was added.

### What was built
| File | Purpose |
|---|---|
| `backend/src/clients/groq_client.py` | Groq wrapper: retry/backoff, error types |
| `backend/src/core/structured_output.py` | Generic schema-forced tool call with repair |
| `backend/src/core/prompts.py` | Ticker extraction prompt |
| `backend/src/core/tool_calling.py` | Request → ticker → market data; CLI |
| `backend/tests/test_groq_client.py` | 11 offline tests + 1 live |
| `backend/tests/test_tool_calling.py` | 15 offline tests + 4 live |
| `backend/src/config.py`, `CLAUDE.md`, `README.md` | gpt-oss model names |
| `backend/requirements.txt` | + groq 1.7.0 |

### How it was tested
- `pytest -q` → **77 passed, 6 deselected** (51 earlier + 26 new).
- `pytest -m live` → **6 passed**: Groq chat; Apple→AAPL, Tesla→TSLA, Berkshire class B→BRK-B; Microsoft end to end; yfinance AAPL.
- CLI: "How is Apple doing?" → AAPL snapshot. "Berkshire Hathaway B shares" → BRK-B snapshot. "Reliance Industries" → `ToolCallError` after the repair attempt (first a refusal, then an invalid ticker), exit 1.

### Next
Stage 3: the LangGraph Analyst/Critic loop with the `consensus_check` iteration guardrail and forced-exit tests. Uses `call_with_schema` for `AnalystOutput` / `CriticFeedback` and `REASONING_MODEL`.

## Stage 3: LangGraph Analyst/Critic loop with iteration guardrail

### Approved plan (with the owner's change)
1. **Dependency:** `langgraph` pinned.
2. **State:** `GraphState.forced_exit: bool = False`.
3. **Prompts:** `ANALYST_PROMPT` and `CRITIC_PROMPT` share `DATA_UNITS_NOTE`.
4. **Nodes in `core/nodes/`:**
   - `fetch_data` (yfinance, injectable factory);
   - `analyst` (`call_with_schema` → `AnalystOutput`, tool `submit_analysis`, `REASONING_MODEL`; revisions see the previous output and the critique);
   - `critic` (→ `CriticFeedback`, tool `submit_critique`);
   - `consensus_check` (node + pure router).
5. **Owner's change:** `max_iterations=3` means **3 Analyst rounds in total**. `iteration_count` is the number of completed rounds, and `forced_exit` is set when the last round ends without agreement.
6. **`core/graph.py`:** `build_graph`, `run_research`, `stream_research`, a CLI, and a `recursion_limit` derived from the cap.
7. **Tests:** `test_guardrail.py` (node/router unit tests + whole-graph tests with a fake LLM), `test_nodes.py`, and one live test.
8. **Docs and git:** DESIGN, DECISIONS D24-D28, README, PROGRESS, DEVLOG. Branch `stage/3-debate-loop`, tag `stage-3-debate-loop`.
9. **Out of scope:** the Auditor (Stage 5; `done` goes to END for now) and filing chunks (Stage 4).

### What was built
| File | Purpose |
|---|---|
| `backend/src/core/state.py` | + `forced_exit` |
| `backend/src/core/prompts.py` | `DATA_UNITS_NOTE`, `ANALYST_PROMPT`, `CRITIC_PROMPT` |
| `backend/src/core/nodes/fetch_data.py`, `analyst.py`, `critic.py` | Graph nodes |
| `backend/src/core/nodes/consensus_check.py` | Guardrail: `consensus_check` node + `route_after_consensus` |
| `backend/src/core/graph.py` | Graph build/compile, `run_research`, `stream_research`, CLI |
| `backend/tests/fakes.py` | `ToolRoutingClient`: a fake LLM scripted per tool and per round |
| `backend/tests/test_guardrail.py` | 21 offline tests + 1 live test |
| `backend/tests/test_nodes.py` | 9 node tests |
| `CLAUDE.md`, `docs/DESIGN.md` | Updated guardrail semantics |

### How it was tested
- `pytest -q` → **107 passed, 7 deselected**.
- Guardrail tests:
  - A Critic that never agrees with cap 3 gives exactly 3 Analyst and 3 Critic calls, `iteration_count=3` and `forced_exit=True`.
  - Agreement in round 1 or 2 stops early, and agreement in round 3 is not marked as forced.
  - Cap 10 terminates.
  - A router that always returns `continue` (monkeypatched) is stopped by the recursion limit with `GraphRecursionError`.
- `pytest -m live` → **7 passed** (including a 1-round live debate).
- CLI:
  - `python -m src.core.graph AAPL 3` reached consensus in round 2. In round 1 the Critic pointed out that quarterly revenue growth had been presented as annual growth, and the Analyst corrected it.
  - `MSFT 2` reached consensus in round 1.

### Next
Stage 4: RAG over SEC filings. Parse the 10-K, chunk it semantically, store it in ChromaDB with metadata filtering, retrieve passages into `filing_chunks`, and have the Analyst cite them.

## Stage 4: RAG over SEC filings

### Approved plan (with the owner's choices)
1. **Owner's choices:** option A, so EDGAR 10-K HTML downloads plus optional manual PDFs. `SEC_USER_AGENT` lives only in `backend/.env`; `.env.example` has a placeholder.
2. **`rag/edgar.py`:** looks up ticker → CIK, finds the latest 10-K, and downloads it with the SEC User-Agent, throttled and cached in `data/filings/`.
3. **`rag/ingest.py`:** HTML/PDF text extraction, 10-K Item sections, section-aware semantic chunking, deterministic ids and metadata.
4. **`rag/retriever.py`:**
   - a ChromaDB `FilingStore` (upsert, latest filing, pre-filtered query);
   - coverage-first retrieval over 6 topic queries;
   - lazy ingestion from EDGAR.
5. **`structured_output.call_with_schema`:** an `extra_check` hook, used to reject invented citations.
6. **Wiring:** `fetch_data` fills `filing_chunks`; a missing filing gives `[]` and a warning. Analyst and critic share one evidence block, and the analyst cites chunk ids.
7. **Tests and docs:** offline tests with an in-memory Chroma and a fake embedder, plus live tests. DECISIONS D29–D34, and a RAG section in DESIGN.
8. **Git:** branch `stage/4-rag`, tag `stage-4-rag`.

### What was built
| File | Purpose |
|---|---|
| `backend/src/rag/edgar.py` | EDGAR client (User-Agent, throttle, cache, errors) |
| `backend/src/rag/embeddings.py` | `Embedder` protocol + `LocalEmbedder` (MiniLM via ONNX) |
| `backend/src/rag/ingest.py` | Extraction, sections, semantic chunking, chunk ids; PDF CLI |
| `backend/src/rag/retriever.py` | `FilingStore`, `retrieve_for_agents`, `get_filing_chunks`; CLI |
| `backend/src/core/nodes/context.py` | Shared evidence block (compact market JSON + excerpts), chunk-id parsing |
| `backend/src/core/nodes/fetch_data.py`, `analyst.py`, `critic.py`, `graph.py` | RAG wiring and citation check |
| `backend/src/core/structured_output.py` | `extra_check` hook |
| `backend/src/core/prompts.py` | `FILING_RULES_NOTE`, citation rules |
| `backend/src/clients/groq_client.py` | Retry cap 60s, 4 attempts (D35) |
| `backend/tests/test_edgar.py`, `test_ingest.py`, `test_retriever.py` | 10 + 15 + 10 offline tests, 1 live |
| `backend/tests/test_nodes.py`, `test_tool_calling.py`, `test_groq_client.py` | New tests: filing, citation, `extra_check` and retry |
| `.gitignore`, `CLAUDE.md` | `**/data/filings/` fix |

**Changes from the plan:**
- Added D35 and a fix commit for the Groq token limit, after a live debate failed.
- Added the `.gitignore` anchoring fix.
- The live citation check went into the existing live debate test instead of a separate test.

### How it was tested
- `pytest -q`: **156 passed, 8 deselected**.
- `pytest -m live`: **8 passed**, including:
  - the real AAPL 10-K ingested from EDGAR (139 chunks);
  - a risk query that returns only Item 1A chunks;
  - a live debate whose citations are all among the provided ids.
- `python -m src.rag.retriever AAPL "supply chain and manufacturing risk"`: all of the top 5 hits are relevant Item 1A passages.
- `python -m src.core.graph AAPL 2`: consensus in round 2 after 105s.
  - Round 1 cited 4 chunks. The critic flagged filing risks the analyst had ignored (legal proceedings, natural disasters).
  - Round 2 added citations `1A-010` and `3-005` and corrected annual revenue growth to about 6%.

### Next
Stage 5 has two parts:
- An Auditor (LLM-as-a-Judge) that checks the final claims against the cited filing text and sets `auditor_verdict`.
- A standalone eval harness in `backend/evals/`: a golden set, scored runs, and judge calibration.

## Stage 5: Auditor (LLM-as-a-Judge) + eval harness

### Approved plan
1. **Auditor node** (`core/nodes/auditor.py`, `AUDITOR_PROMPT`):
   - It sees the final analysis and the same evidence the agents had, but not the critic's feedback.
   - It uses tool `submit_verdict` on `REASONING_MODEL`.
   - An `extra_check` makes sure `is_grounded` agrees with `unsupported_claims`.
2. **Graph:** `done → auditor → END`, for both consensus and forced exit. The recursion limit now includes the auditor step, and the CLI prints the verdict and labels "UNRESOLVED DISAGREEMENT".
3. **`backend/evals/`** (standalone):
   - frozen AAPL fixture plus `capture_fixture.py`;
   - `golden_set.json` with 4 extraction, 7 judge and 3 pipeline cases, validated by `golden.py`;
   - `metrics.py`, `runner.py`, `run_evals.py` (suites: extraction / judge / pipeline / all);
   - `judge_calibration.py` (confusion matrix, precision/recall with "ungrounded" as the positive class, claim recall, agreement over `--repeats`).
4. **Tests:** `test_auditor.py`, updated graph tests, `test_evals.py` (golden-set validity, metric math, runners with a fake LLM), and the live debate test now asserts the auditor ran.
5. **Docs and git:** DESIGN, DECISIONS D36–D40, README, PROGRESS, DEVLOG, and `**/evals/results/` in `.gitignore`. Branch `stage/5-auditor-evals`, tag `stage-5-auditor-evals`.

### Changes from the plan
- **Judge case swapped:** the case "fact from the filing with no citation" was replaced by "outside knowledge" (foldable iPhone). The original label was ambiguous, because the uncited fact *is* supported by an excerpt.
- **Prompt fix:** reworded `debt_to_equity` in the units note (Stage 4 DEVLOG: the critic raised an invalid "78.4%" objection). It has its own commit.
- **README fix:** fixed the corrupted PDF ingest command shipped in the Stage 4 README (escape sequences). It has its own commit.
- **Eval runs:** ran `judge_calibration` once instead of `run_evals --suite all`, to avoid auditing the judge cases twice against Groq's daily token limit.

### What was built
| File | Purpose |
|---|---|
| `backend/src/core/nodes/auditor.py` | Auditor node, consistency check |
| `backend/src/core/prompts.py` | `AUDITOR_PROMPT`; debt_to_equity wording |
| `backend/src/core/graph.py` | Auditor wired after the loop; recursion limit; CLI verdict |
| `backend/evals/golden.py`, `golden_set.json` | Golden set schema and 14 cases |
| `backend/evals/fixtures/aapl_snapshot.json`, `capture_fixture.py` | Frozen evidence |
| `backend/evals/metrics.py`, `runner.py`, `run_evals.py`, `judge_calibration.py` | Harness |
| `backend/tests/test_auditor.py` | 10 tests |
| `backend/tests/test_evals.py` | 22 tests |
| `backend/tests/test_guardrail.py` | Auditor in graph tests |
| `.gitignore` | `**/evals/results/` |

### How it was tested
- `pytest -q` → **188 passed, 8 deselected**.
- `pytest -m live tests/test_guardrail.py` → passed: a 1-round AAPL debate with the auditor and valid citations.

**Live eval results (2026-10-09/10, Groq free tier):**

| Suite | Result |
|---|---|
| Extraction | **4/4**. Note: for Reliance the model invented `RIL`, which only failed because it doesn't exist (DEVLOG) |
| Judge calibration | **7/7**: precision 1.00, recall 1.00, claim recall 1.00 (TP 5, FP 0, FN 0, TN 2) |
| Pipeline (max 2 rounds) | **3/3** passed all code checks; average 168s per run; forced exits 2/3 (MSFT, JPM); auditor marked all 3 final analyses ungrounded |

- **Manual review of the 9 claims the auditor flagged in the pipeline runs:** about 6–7 are valid and 2 are false flags caused by the auditor's own arithmetic or unit mistakes. Precision on real outputs is about 0.67–0.78, against 1.00 on the golden set. The golden set is too easy; see the DEVLOG for details and next steps.

### Next
Stage 6: FastAPI backend.
- Google ID token auth.
- A per-user quota checked before the graph runs.
- Persistence of runs and events (user-scoped).
- SSE streaming of `stream_research` updates.

This needs the Google OAuth client ID.

## Stage 6: FastAPI backend (auth, quota, persistence, SSE)

### Approved plan
1. **Flow:** `POST /research` → verify the Google token → check quota → extract the ticker → create the run (pending) → hand it to the background worker → 202. The SSE stream replays the persisted `run_events` until `end`.
2. **Decisions approved:**
   - the graph runs in an in-process worker (2 runs at once overall), not inside the request;
   - SSE uses the Authorization header (the frontend reads it with `fetch`);
   - quota: 1 active run + 5 runs per rolling 24h, checked before any LLM call, and a failed extraction costs nothing;
   - `run_events.step` gains `fetch_data`, `consensus_check` and `error`.
3. **Files:** `api/auth.py`, `api/quota.py`, `api/jobs.py`, `api/routes_research.py`, `api/deps.py`, `main.py` (`create_app` factory), plus the DB helpers and config.
4. **Tests:** `test_auth.py`, `test_quota.py`, `test_api.py` (plus `test_jobs.py`); live tests: the real Google verifier rejects a forged token, and a full live run through the API.
5. **Docs and git:** DESIGN section 11, DECISIONS D41–D47, README, the CLAUDE.md spec. Branch `stage/6-api`, tag `stage-6-api`.

### What was built
| File | Purpose |
|---|---|
| `backend/src/api/auth.py` | `verify_google_token` (google-auth, plus issuer and email_verified checks), `get_current_user` (create/update the user, 401 on any failure) |
| `backend/src/api/quota.py` | `check_quota`: 1 active run, 5 per 24h, HTTP 429 |
| `backend/src/api/jobs.py` | `RunWorker`: thread pool, one committed event per graph step, `final_output`, user-safe error messages |
| `backend/src/api/routes_research.py` | `POST /research`, `GET /research/{id}`, `GET /research/{id}/stream` (SSE), `GET /history`, `GET /me` |
| `backend/src/api/deps.py` | `get_db` from `app.state.session_factory` |
| `backend/src/main.py` | `create_app()` with injected collaborators; CORS; `/health`; startup marks stale runs failed |
| `backend/src/db/models.py` | New `RunStep` values; `get_events_for_run`, `count_runs_since`, `count_active_runs`, `mark_stale_runs_failed` |
| `backend/src/db/session.py` | `StaticPool` for in-memory SQLite (tests); `make_session_factory` |
| `backend/src/config.py` | Quota and concurrency limits |
| `backend/tests/test_auth.py`, `test_quota.py`, `test_jobs.py`, `test_api.py`, `api_helpers.py` | 18 + 7 + 12 + 27 tests (+2 live) |
| `CLAUDE.md` | `run_events.step` spec |

**Changes from the plan:**
- Extra commit `refactor(db): share in-memory SQLite across threads`. API tests need one in-memory DB shared across threads.
- `GET /research/{id}/stream` ends with `status: "timeout"` after 30 minutes, as a safety net for stuck runs.

### How it was tested
- `pytest -q`: **259 passed, 10 deselected**.
- **Live:**
  - `test_live_google_rejects_garbage_token`: the real google-auth rejected a forged JWT. Passed.
  - `test_live_full_api_run`: real extraction ("How is Apple doing?" → AAPL), then a 1-round debate with RAG and the auditor, running on a real thread pool against a temp SQLite file. The streamed steps were `fetch_data, analyst, critic, consensus_check, auditor, end(done)`. Passed in about 4 minutes, including one rate-limit wait.
- **Manual, with `uvicorn src.main:app --port 8765`:**
  - `/health` returned 200;
  - `POST /research` with no token returned 401 "Missing Authorization header";
  - with a forged token it returned 401 "Invalid or expired Google ID token" (real Google verification);
  - `/history` with no token returned 401;
  - a CORS preflight from `http://localhost:5173` was allowed;
  - OpenAPI lists all 6 routes.
- **Not yet tested:** a real Google sign-in end to end. That needs the Stage 7 frontend to obtain a real ID token.

### Next
Stage 7: React (Vite) frontend.
- Google sign-in.
- Submit a ticker or question.
- A live Analyst/Critic/Auditor stream via fetch-based SSE.
- Per-user history.
