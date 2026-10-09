# Design Decisions

Each entry: the choice, the alternatives considered, and the trade-off.

## Stage 0

### D1. GraphState is a Pydantic `BaseModel`
- **Alternatives:** `TypedDict` (LangGraph's most common style), dataclass.
- **Why:** LangGraph accepts Pydantic state. We get type validation, defaults (`iteration_count=0`, `max_iterations=3`) and nested parsing of agent outputs for free, and it matches the other schemas.
- **Trade-off:** slightly slower than a plain dict, and nodes must return partial-update dicts rather than mutating state. Negligible at our scale.

### D2. LLM-output schemas forbid extra fields (`extra="forbid"`)
- **Alternatives:** Pydantic default (`ignore`), or `allow`.
- **Why:** if the model invents a field (e.g. `price_target`) we want to know, not silently drop it. It also surfaces prompt/schema drift early.
- **Trade-off:** more validation failures, so Stage 2 needs a retry / repair step.

### D3. Ticker input: normalize, then validate
- **Alternatives:** strictly reject lowercase input; accept anything and let yfinance fail.
- **Why:** `aapl` is an obvious typo for `AAPL`, so we uppercase it. We still reject empty input, exchange suffixes (`RELIANCE.NS`) and anything outside `^[A-Z]{1,5}(-[A-Z])?$`. The `-B` form allows class shares like `BRK-B` (yfinance format).
- **Trade-off:** rejects some valid non-US or unusual symbols. That matches the scope (US filings only).

### D4. `users.id` is the Google `sub` claim
- **Alternatives:** auto-increment id + email unique; email as the key.
- **Why:** `sub` is Google's stable, unique account id; email can change. Using it as the PK avoids an extra lookup on every request.
- **Trade-off:** ties identity to Google. Adding another provider later would need a separate identities table.

### D5. Research run ids are random UUID strings
- **Alternatives:** auto-increment integers.
- **Why:** run ids appear in URLs (`/research/{id}/stream`). Sequential ids invite enumeration. We still check ownership on every read; UUIDs are defence in depth.
- **Trade-off:** larger keys, less readable in logs.

### D6. Every run read goes through user-scoped helpers
- **Alternatives:** ad-hoc queries in each route; Postgres row-level security.
- **Why:** one place (`get_runs_for_user`, `get_run_for_user`) to test the "no cross-user reads" rule. RLS would be stronger but ties us to Postgres and complicates local SQLite.
- **Trade-off:** relies on discipline: routes must use the helpers.

### D7. Enums stored as strings (`native_enum=False`)
- **Alternatives:** native Postgres ENUM types.
- **Why:** identical behaviour on SQLite and Postgres; adding a status later needs no `ALTER TYPE`.
- **Trade-off:** the DB stores plain strings with no CHECK constraint, so only the app (SQLAlchemy's Enum type) enforces the allowed values.

### D8. `create_all()` now, migrations (Alembic) in Stage 8
- **Alternatives:** Alembic from day one.
- **Why:** the schema will still change during Stages 1-7 and there is no production data yet.
- **Trade-off:** schema changes before Stage 8 require deleting the local `app.db`.

### D9. SQLite locally, Postgres when deployed, switched by `DATABASE_URL`
- **Alternatives:** Postgres in Docker locally.
- **Why:** zero setup for local development and tests (in-memory SQLite). SQLAlchemy hides most differences. Foreign keys are turned on in SQLite with a connect pragma so behaviour matches Postgres.
- **Trade-off:** small dialect differences (e.g. SQLite returns naive datetimes) can hide bugs until deployment.

### D10. Dependencies are added per stage
- **Alternatives:** install the full stack (LangGraph, ChromaDB, FastAPI…) up front.
- **Why:** each stage's `requirements.txt` reflects what is actually used, and installs stay fast.
- **Trade-off:** `requirements.txt` changes in most stages.

### D11. Reasoning model: `llama-3.3-70b-versatile`
- **Alternatives:** a larger Llama-3.1 model as CLAUDE.md originally suggested.
- **Why:** Groq retired the Llama-3.1-70B model; 3.3-70B is its replacement in the same family. The tool-calling model stays `llama-3.1-8b-instant`. Model names live in `config.py` so they are easy to change. Will be re-checked against Groq's model list in Stage 2.
- **Trade-off:** none significant; may change again as Groq updates models.

## Stage 1

### D12. Curated market data fields, not the full `Ticker.info` dump
- **Alternatives:** pass all ~150 `info` keys to the LLM.
- **Why:** a stable, documented shape (`profile`, `price`, `valuation`, `financials`, `annual_financials`, `price_history`) costs far fewer tokens and is predictable for prompts, tests and the UI. The mapping lives in one constant (`INFO_FIELDS`).
- **Trade-off:** a field the LLM might find useful is left out until we add it to the mapping.

### D13. Output is JSON-safe plain Python
- **Alternatives:** return pandas DataFrames / numpy values.
- **Why:** the same dict goes into `GraphState.raw_data`, the `run_events` JSON column and the SSE stream. `_clean()` converts numpy scalars and turns NaN/inf into `None` (NaN is not valid JSON).
- **Trade-off:** conversion code to maintain; no DataFrame operations downstream (we don't need them; reasoning is the LLM's job).

### D14. Inject the yfinance `Ticker` factory instead of mocking yfinance
- **Alternatives:** `unittest.mock.patch("yfinance.Ticker")`, recorded HTTP fixtures (VCR).
- **Why:** `fetch_market_data(ticker, ticker_factory=yf.Ticker)` lets tests pass a small `FakeTicker`. Tests don't depend on yfinance internals or module paths, and they run offline.
- **Trade-off:** fakes can drift from real yfinance behaviour, so one opt-in live test checks the real thing (D15). The fake for an unknown ticker copies what yfinance really returned.

### D15. Network tests are opt-in (`pytest -m live`)
- **Alternatives:** always run them; no live tests at all.
- **Why:** the default suite must be fast, deterministic and work offline (and in CI later). A live test still exists to catch yfinance API changes.
- **Trade-off:** the live test must be run deliberately.

### D16. Typed exceptions instead of returning `None`
- **Alternatives:** return `None` or an `{"error": ...}` dict.
- **Why:** callers need to tell "bad input" (`InvalidTickerError`, user should fix it) from "unknown symbol" (`TickerNotFoundError`) from "source is down" (`DataSourceError`, retry later). Stage 6/8 map these to HTTP 422 / 404 / 503.
- **Trade-off:** callers must catch them; `MarketDataError` is a common base so one `except` is enough when the distinction doesn't matter.

### D17. Keep yfinance's units, but name them explicitly
- **Alternatives:** convert everything to fractions.
- **Why:** yfinance 1.x returns `dividendYield` in percent (0.32 = 0.32%) but `revenueGrowth`/`profitMargins` as fractions (0.164 = 16.4%). Renaming the field to `dividend_yield_pct` makes the unit visible to the LLM without silently transforming data. Units are documented next to `INFO_FIELDS`.
- **Trade-off:** mixed units remain; prompts in Stage 3 must mention them.
