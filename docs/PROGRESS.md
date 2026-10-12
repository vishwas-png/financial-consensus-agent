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

## Stage 7: React frontend

### Approved plan (with the owner's choices)
1. **Owner's choices:** TypeScript, and confirmed that `http://localhost:5173` is an authorized JavaScript origin for the OAuth client.
2. **Libraries:** Vite + React + TypeScript; `react-router-dom`; Vitest + Testing Library + jsdom; plain CSS.
3. **Sign-in:** Google Identity Services used directly (`AuthProvider`, `GoogleSignInButton`). The token is held in memory and `sessionStorage`, and the user is signed out automatically at expiry or on any 401.
4. **API layer:** `api/types.ts` mirrors the backend responses; `api/client.ts` adds the Bearer token, turns errors into `ApiError`, and signs out on 401; `api/sse.ts` is a fetch-based SSE parser plus `streamRun`, which reconnects with `Last-Event-ID`.
5. **Run view:** `runs/runReducer.ts` (events → rounds, outcome, auditor; deduplicated by event id) and `runs/RunView.tsx`.
6. **Pages:** `LoginPage`, `NewResearchPage`, `RunPage` (`/runs/:id`), `HistoryPage`.
7. **End-to-end check:** the owner signs in with a real Google account and runs AAPL with 1 round.
8. **Docs and git:** README, DESIGN section 12, DECISIONS D48–D52, PROGRESS, DEVLOG. Branch `stage/7-frontend`, tag `stage-7-frontend`.

### What was built
| File | Purpose |
|---|---|
| `frontend/` scaffold | Vite 8, React 19, TypeScript 6, oxlint, Vitest 5; dev server pinned to port 5173 |
| `frontend/src/api/types.ts`, `client.ts`, `sse.ts`, `useApi.ts` | Typed API client, SSE over fetch with reconnect |
| `frontend/src/auth/token.ts`, `google.ts`, `context.ts`, `AuthProvider.tsx`, `GoogleSignInButton.tsx` | Google sign-in, token lifecycle |
| `frontend/src/runs/runReducer.ts`, `RunView.tsx` | Live and replayed run rendering |
| `frontend/src/pages/*.tsx`, `App.tsx`, `styles.css` | Pages, routing, styling (dark mode, responsive) |
| `frontend/src/**/*.test.ts(x)`, `src/test/*` | 63 tests: SSE parser and stream, API client, auth, reducer, RunView, pages |
| `frontend/.env.example` | `VITE_API_URL`, `VITE_GOOGLE_CLIENT_ID` |

**Changes from the plan:**
- Added an extra fix commit for an oxlint warning (`react/set-state-in-effect`) that was committed by mistake.
- Added DECISIONS D53 (plain CSS and the router).
- The New research page defaults to 2 rounds instead of the backend's 3, so runs are faster on the free tier.

### How it was tested
- `npm test`: **63 passed**. `npm run lint`: clean. `npm run build`: type check passed and the production bundle is about 276 kB (88 kB gzipped).
- Backend `pytest -q`: unchanged, **259 passed**.
- **Real end-to-end test (2026-10-10), with the owner signed in with Google at `http://localhost:5173`:**
  - Two runs were made. AAPL reached consensus in round 1. NVDA hit the round limit with the critic still objecting, so it showed as unresolved disagreement.
  - Both streamed every step, citation chips opened the 10-K excerpts, and History listed both runs.
  - The backend log showed only 200/202 responses and 4 Groq rate-limit retries, all of which succeeded. There were no other errors.
  - The auditor marked both final analyses as not fully grounded, the same pattern as in the Stage 5 evals.

### Next
Stage 8: deployment and hardening.
- Hosted Postgres.
- Backend on Render, Railway or Fly.io, and frontend on Vercel or Netlify, with production origins.
- Secrets management.
- Pre-downloading the embedding model.
- Better rate-limit and timeout handling.
- A CSP header.
- An atomic quota check.
- Alembic migrations.

## Stage 8: Deployment + hardening

### Approved plan
- **Hosting (all free):**
  - **Neon** Postgres;
  - **Render** web service (Docker) for the backend, with `MAX_CONCURRENT_RUNS=1` on 512 MB (about 231 MB measured locally with everything loaded);
  - **Vercel** for the frontend.
  - The repo stays private.
- **From the owner:**
  - create Neon, Render and Vercel accounts (sign in with GitHub) and give Render and Vercel access to the repo;
  - paste secrets only into the dashboards;
  - add the Vercel URL to Google's Authorized JavaScript origins.
- **Hardening:**
  1. Postgres support and Alembic migrations.
  2. Atomic quota check (row lock).
  3. Production config: refuse to start if a setting is missing, multiple CORS origins, security headers, logging.
  4. Groq request timeout and an overall run timeout.
  5. A `no_us_listing` option for ticker extraction.
  6. Dockerfile (model downloaded at build time), `render.yaml`, `vercel.json` with a CSP.
  7. GitHub Actions CI.
  8. Docs.
- **Verification:**
  - offline tests;
  - Postgres tests (migrations, concurrent quota race);
  - a local Docker image (health check, model already present, peak memory during a first 10-K ingestion);
  - a deployed smoke test with the owner signing in.

### Change to the plan (owner decisions)
- **Docker image verification:** Docker Desktop couldn't run locally (its WSL VM was missing, and BIOS virtualization couldn't be changed), so the image is verified in GitHub Actions instead (D60).
- **Embeddings:** first-time 10-K ingestion was far too slow on Render's ~0.1 CPU.
  - The owner chose a hosted embedding API.
  - Gemini's free tier was measured too slow (586 s per 10-K).
  - The owner then chose Cohere, measured at 27–29 s per 10-K (D62).

### What was built
- **Database:**
  - Postgres support via psycopg 3;
  - Alembic migrations (the initial schema), run at container start;
  - `normalize_database_url` for `postgres://` URLs.
- **Quota and runs:**
  - atomic quota: a row lock on the user, then a re-check before inserting the run;
  - a race-safe first sign-in (`get_or_create_user`);
  - stuck runs expire after 15 minutes (`expire_stuck_runs`), and the worker stops when its run has been expired.
- **Production config:**
  - `APP_ENV=production` refuses to start when something is missing or malformed: secrets, a SQLite URL, malformed or `http://` origins, the Cohere key;
  - settings are trimmed of pasted whitespace;
  - multiple CORS origins, logged at startup;
  - security headers;
  - `UnhandledErrorMiddleware` (crash 500s keep their CORS headers).
- **Timeouts:**
  - Groq: 60 s per request, with auth and HTTP errors wrapped in `LLMError` types;
  - filing ingestion: at most 120 s, then the run continues without excerpts;
  - run timeout 600 s; run expiry 15 minutes.
- **Embeddings:**
  - `EMBEDDING_PROVIDER=local|cohere`: `CohereEmbedder` (batches of 96, document and query input types, retry with backoff);
  - one Chroma collection per model;
  - local embedding batches of 8 to stay under 512 MB;
  - timing logs for each ingestion phase.
- **Short database transactions:** reads commit right after auth and between worker steps, so Neon never sees an idle transaction.
- **Ticker extraction:** `no_us_listing` lets the model decline non-US companies, returning a 422 that uses no quota.
- **Deployment files:**
  - `backend/Dockerfile` (non-root; the local model is baked in at build time) and `.dockerignore`;
  - `render.yaml`;
  - `frontend/vercel.json` (CSP and headers) with `deploy.test.ts`;
  - `.github/workflows/ci.yml`: backend (offline and Postgres 17), frontend, and the Docker image with in-container probes.
- **Deployed:**
  - Neon (Postgres 17, Oregon);
  - Render: `https://financial-consensus-api.onrender.com`;
  - Vercel: `https://financial-consensus-agent.vercel.app`.

### How it was tested
- **Backend:** 344 offline tests pass. Postgres-marked tests run in CI (migrations up/down/up with a drift check, and the concurrent quota race). Opt-in live tests for Groq and Cohere pass.
- **Frontend:** 67 tests pass; lint, type check and build are clean.
- **CI:** the last run on `stage/8-deploy` passed all jobs (backend, frontend, docker-image). The image probe measured a peak of 416 MB, under 512 MB.
- **"Fails without the fix" checks:** these new tests were run against the code with the fix removed, to confirm they fail:
  - the quota race;
  - the parallel first sign-in;
  - the stuck-run expiry;
  - the 500 with CORS headers;
  - no open transactions after auth and during worker steps.
- **Measurements:**
  - **local embedding:** a MSFT 10-K took about 3.5 minutes on a laptop;
  - **Gemini free tier:** 586 s;
  - **Cohere:** MSFT 27 s, AAPL 29 s.
- **Live smoke test:**
  - deployed `/health` answers 200, `/history` without sign-in gives 401, and the security headers are present;
  - CORS allows the Vercel origin and localhost and rejects other origins;
  - the owner signed in on the Vercel site and ran AAPL (1 round) to a full result after the final deploy.
- **Live problems found and fixed:** 7 of them, in DEVLOG.md (local) and D57/D58/D62/D63:
  - CORS origins rejected;
  - pasted whitespace in the Groq key;
  - an invalid key shown as a CORS error;
  - a run stuck on slow ingestion;
  - Gemini quotas;
  - idle-transaction timeouts on Neon;
  - a test reading the developer's `.env`.

### Known limitations carried forward
- The free tier sleeps after 15 idle minutes and wipes its disk on every sleep and deploy; one run executes at a time.
- MSFT's "Item 7" section gets the wrong text: a Stage 4 parsing bug, logged and not fixed yet.
- Cohere trial key: about 1,000 calls a month, and filing text goes to a third party.

### Next
Stage 9 (extensions) starts only when the owner says "plan stage 9".

## Stage 9: Extensions

### Approved plan
Built in order 9a → 9b → 9c on branch `stage/9-extensions`, merged to `main` (which auto-deploys) only after all three are verified.

**9a. Observability**
- **Collector:** a per-run `RunMetrics` collector (context variable; `core/` stays user-agnostic). It records:
  - step durations;
  - LLM calls, retries and tokens (from Groq `usage`);
  - embedding calls;
  - the filing status (`cached` / `indexed` / `timeout` / `none`).
- **Where it surfaces:**
  - `fetch_data` events carry `filing_status`;
  - log lines carry `run=<id>`;
  - migration: `research_runs.metrics` (JSON), returned by `GET /research/{id}`;
  - a "Run details" panel in the UI.

**9b. Extra agent roles**
- **Risk Officer (LLM):** a node between `consensus_check` and `auditor`.
  - Output: `RiskAssessment`, with `key_risks` (each with description, severity and a filing citation), `what_would_change_the_thesis`, and `overall_risk`.
  - Citations are validated, with one repair attempt.
- **Data Checker (code, no LLM):** inside the auditor step. It compares the analyst's `metric = value` data points with `raw_data`, within a tolerance.
- **Plumbing:** a new `RunStep.RISK_OFFICER`; UI and evals updated.

**9c. Multi-ticker comparison**
- **Extraction:** `TickerListExtraction` takes 2–3 tickers, with `no_us_listing` handling.
- **Core:** `stream_comparison` runs the existing graph per ticker (1 round each, events tagged by ticker), then a Comparator LLM node produces `ComparisonOutput` with validated citations.
- **API:** `POST /compare` creates one run with `kind=compare` and `tickers`.
  - Quota counts it as N runs, under the row lock.
  - The run timeout and expiry scale with N.
- **UI:** compare mode, per-ticker tabs plus the verdict, and history shows "AAPL vs MSFT".
- **Evals:** 3 extraction golden cases.

**Git and verification**
- **Commits:**
  1. feat(obs) metrics;
  2. feat(ui) details panel;
  3. feat(core) Risk Officer;
  4. feat(core) Data Checker;
  5. feat(core) comparison;
  6. feat(api) /compare;
  7. feat(ui) compare mode;
  8. test(evals);
  9. docs.
- **Verification:**
  - offline tests;
  - live opt-in tests for the Risk Officer and the Comparator;
  - evals;
  - CI (with Postgres migrations);
  - deploy;
  - owner smoke test: one single run and one 2-ticker comparison.
- **Finish:** tag `stage-9-extensions`, run the secret scan, then push.

### What was built
- **9a. Observability**
  - `src/observability.py`: the `RunMetrics` collector, `run_context` and `RunIdLogFilter`. The Groq client, embedders and ingestion report to it; the worker records step times.
  - Migration `aebcb5af70f2` adds `research_runs.metrics`.
  - The UI has a **Run details** panel, and the data card explains a missing 10-K (`filing_status`).
- **9b. Extra agent roles**
  - `core/nodes/risk_officer.py` with `RISK_OFFICER_PROMPT` and the `RiskAssessment` schema (validated citations).
  - `core/data_checker.py`: code-only checks of `field = value` data points, run inside the auditor step.
  - Graph: `consensus_check → risk_officer → auditor`.
  - The UI has a Risk Officer card and a data-check summary.
  - Evals: `data_check` (12 cases) and `risk` (2 live cases).
- **9c. Multi-ticker comparison**
  - **Core:** `extract_tickers` (`TickerListExtraction`) and `core/comparison.py` (`stream_comparison` plus the Comparator with `ComparisonOutput`).
  - **Data and quota:** migration `673ff4671f7b` adds `kind`, `tickers` and `units`, and widens `ticker` to 40 characters. The quota sums units; timeouts and expiry scale with N.
  - **API:** `POST /compare`.
  - **Worker:** one shared save-each-step loop for single runs and comparisons.
  - **UI:** compare mode, `compareReducer`, and `CompareRunView` with tabs and a Comparison card.
  - **Evals:** `compare_extraction` (4 cases).

### How it was tested
- **Backend:** 431 offline tests pass. Opt-in live tests pass for the Risk Officer, ticker-list extraction and Cohere.
- **Frontend:** 95 tests pass; type check, lint and build are clean.
- **"Fails without the fix" checks:** a comparison timeout test (the same slow run fails with a smaller budget).
- **Live:**
  - a 1-round AAPL debate with the Risk Officer (valid citations) and the Data Checker (9 match, 0 mismatch, 1 unchecked);
  - an AAPL vs MSFT comparison end to end (it exposed the wrong "net cash" number, now fixed in the prompt).
- **Evals, live:** data_check 12/12, risk 2/2, compare_extraction 4/4.
- **CI on `stage/9-extensions`:** passed after the backend commits (backend with Postgres migrations, frontend, docker-image).
- **Deployed and smoke-tested by the owner on the live site:**
  - a single AAPL run (Risk Officer card, Run details: 110 s, 10-K indexed in 13.7 s via Cohere);
  - an AAPL vs MSFT comparison ("prefers AAPL").
  - One layout bug in the History row was found and fixed.

### Status
All planned stages (0-9) are complete. Known limitations are in the README; the DEVLOG (local) has every failure.
