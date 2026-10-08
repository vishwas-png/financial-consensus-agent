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
