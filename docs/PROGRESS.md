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
