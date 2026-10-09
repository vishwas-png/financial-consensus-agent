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

### D11. Reasoning model: `llama-3.3-70b-versatile` (superseded by D23)
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

## Stage 2

### D18. Our own retry loop; SDK retries disabled
- **Alternatives:** the Groq SDK's built-in retries (`max_retries=2` by default); a library such as `tenacity`.
- **Why:** one explicit loop in `groq_client.py` that can be read in a minute and is unit tested: what is retried (429, 5xx, connection/timeout), the backoff (1s, 2s, ...), `retry-after` support (capped at 30s), and what is never retried (400/401/404). Leaving SDK retries on too would multiply attempts (3 x 3).
- **Trade-off:** we maintain ~30 lines the SDK already has.

### D19. Forced tool call (`tool_choice`) for structured output, not JSON mode
- **Alternatives:** `response_format={"type": "json_object"}`; prompt-only "reply in JSON"; Groq's strict JSON-schema mode.
- **Why:** forcing a specific function makes the model's answer arrive as tool arguments with our JSON Schema attached, and Groq returns a clear `tool_use_failed` error when the model can't comply. The same mechanism works for both tool calling (ticker) and agent outputs (Stage 3).
- **Trade-off:** we still validate with Pydantic ourselves; the API does not guarantee schema-valid arguments.

### D20. Exactly one repair attempt
- **Alternatives:** fail immediately; retry N times; retry silently without feedback.
- **Why:** showing the model its own output plus the exact validation error fixes most mistakes on the next call (e.g. `BRK.B` -> `BRK-B`). More attempts add cost and latency and, for impossible requests (non-US company), only push the model to invent something.
- **Trade-off:** a mistake the model repeats twice fails the run; the caller gets `ToolCallError` with the reason and raw output.

### D21. One generic `call_with_schema` helper
- **Alternatives:** separate parsing code per agent.
- **Why:** ticker extraction, Analyst, Critic and Auditor all need "call the LLM, get a validated Pydantic object or a clear error". One tested helper means one place for the repair logic.
- **Trade-off:** the helper must stay generic (no finance logic).

### D22. Temperature 0 for extraction
- **Alternatives:** default temperature.
- **Why:** extraction should be deterministic and repeatable for tests and evals; there is no creativity to gain.
- **Trade-off:** none for extraction. Agents in Stage 3 may use a slightly higher temperature if debates become repetitive.

### D23. Models: `openai/gpt-oss-20b` (tool calling) and `openai/gpt-oss-120b` (reasoning)
- **Alternatives:** `qwen/qwen3.8-27b` for both; keep Llama by moving to another provider (Together, Fireworks).
- **Why:** in Stage 2 both Llama ids returned 404 and Groq's `models.list()` had no Llama chat models left. gpt-oss models are open-weight, support tool calling on Groq, and keep the small/large split from the original design. Chosen by the owner.
- **Trade-off:** gpt-oss are reasoning models: they spend tokens thinking before answering, so small `max_completion_tokens` limits produce empty replies. Supersedes D11.

## Stage 3

### D24. `consensus_check` = a node that updates state + a pure router
- **Alternatives:** do everything in the conditional-edge function (as the original spec suggested: "increments `iteration_count` and returns `continue`").
- **Why:** LangGraph routing functions can only pick the next node; any state change they make is discarded. So the node records the round (`iteration_count += 1`, `forced_exit`), and `route_after_consensus` is a pure function of that state, so it is easy to unit test.
- **Trade-off:** one extra graph step per round (counted in the recursion limit, D26).

### D25. Explicit `forced_exit` flag in `GraphState`
- **Alternatives:** infer it later from `iteration_count == max_iterations and not agrees`.
- **Why:** "forced exit" must be shown to users and stored as unresolved disagreement. One explicit, tested field is harder to get wrong than repeating the inference in the API, UI and evals.
- **Trade-off:** one more field than the original GraphState spec.

### D26. `recursion_limit` derived from `max_iterations` as a second safety net
- **Alternatives:** rely on LangGraph's default.
- **Why:** LangGraph 1.2 defaults to **10007** steps (not 25 as in older versions). If the guardrail had a bug, a run could make thousands of LLM calls. We set `1 + 3 x max_iterations + 1`, so a broken router stops within about one extra round with `GraphRecursionError`. A test monkeypatches the router to always `continue` and checks that the run stops.
- **Trade-off:** if nodes are added to the loop, `STEPS_PER_ROUND` must be updated (the large-cap test would fail if it isn't).

### D27. `max_iterations` = total Analyst rounds (owner's decision)
- **Alternatives:** the literal original spec, where `iteration_count` counted revisions, so `max_iterations=3` allowed 4 analyst rounds.
- **Why:** "max_iterations=3 means 3 rounds" is what a reader expects, and it makes cost easy to state: at most `2 x max_iterations` reasoning-model calls (+ repairs). `iteration_count` = completed rounds; `forced_exit` is set when round `max_iterations` ends without agreement. CLAUDE.md and DESIGN.md were updated to match.
- **Trade-off:** differs from the first wording of the spec. Tests cover both edges (agree in the last round = consensus; disagree in the last round = forced).

### D28. Agents get the full market-data JSON plus a units note
- **Alternatives:** a hand-written text summary of the data; only selected fields.
- **Why:** the JSON is already curated (D12) and small (a few thousand tokens). Giving both agents the identical JSON means the critic can check every number the analyst cites. The units note (D17) lives in one constant shared by both prompts.
- **Trade-off:** ~52 weekly price points use tokens on every call; can be down-sampled if cost matters (Stage 8).

## Stage 4

### D29. Filings from SEC EDGAR (HTML) plus optional PDFs (owner's choice)
- **Alternatives:** PDF only (the original spec); EDGAR only.
- **Why:** EDGAR publishes 10-Ks as HTML/inline XBRL, not PDF, so PDF-only would need a manual download for every ticker. EDGAR provides every US filer's latest 10-K automatically. PDF ingestion (`pypdf`) is kept for filings obtained elsewhere, and both paths feed the same chunker.
- **Trade-off:** two extraction paths to maintain. EDGAR also requires a `SEC_USER_AGENT` with a contact email and allows at most 10 requests per second.

### D30. Section-aware semantic chunking
- **Alternatives:** fixed-size chunks with overlap; one chunk per paragraph; LLM-based chunking.
- **Why:** 10-K Items are natural boundaries, and the section becomes metadata for filtering (D32). Inside a section, a break goes where the topic changes: the similarity between neighbouring paragraph embeddings is in the section's lowest 20% and below its median. Chunks are also capped at 1,200 characters. This keeps one risk factor or one MD&A topic together. A size break repeats one paragraph as overlap, a topic break starts clean, and tiny chunks are merged into a neighbour.
- **Trade-off:** more code than fixed-size splitting. The section regex depends on 10-K heading conventions; two quirks are handled: headings repeated in the table of contents, and a period on the next line.

### D31. Local embedding model (all-MiniLM-L6-v2 via ChromaDB's ONNX runtime)
- **Alternatives:** a hosted embedding API (OpenAI, Cohere, Voyage); a larger local model.
- **Why:** it is free, needs no extra API key, runs on CPU, and is good enough for retrieving passages from a single document. We compute the embeddings ourselves (the `Embedder` protocol) and pass them to Chroma. That way the chunker and the store always use the same model, and tests can use a fake embedder.
- **Trade-off:** a one-time ~80 MB download (slow on the first run) and weaker retrieval than larger models. Deployment should download the model in advance (Stage 8).

### D32. Metadata pre-filtering on ticker + accession + section
- **Alternatives:** semantic search over everything, then filter the results; one collection per ticker.
- **Why:** filtering before ranking guarantees that results come from the right filing of the right company, in the right section, and no top-k slots are wasted on chunks that would be thrown away. Each topic query targets one section (risks → 1A, liquidity → 7, and so on).
- **Ranking:** results are coverage-first. Each topic's best hit is taken first, then the remaining slots go to the closest leftover hits, because distances from different queries cannot be compared.
- **Trade-off:** the fixed topic queries are generic, so questions about unusual topics may miss relevant passages.

### D33. Deterministic chunk ids, enforced citations
- **Alternatives:** random ids; citing by quoting text; trusting the model's citations.
- **Why:** ids like `AAPL-10K-2025-1A-007` are readable and easy for the model to copy. They stay the same when a filing is re-ingested, so upserts never create duplicates.
- **Enforcement:** an `extra_check` on `call_with_schema` rejects any citation that is not one of the provided ids and triggers the usual single repair attempt. Invented citations therefore never reach the critic or the auditor.
- **Trade-off:** the check confirms that an id exists, not that the excerpt supports the claim. That is the Critic's job, and the Stage 5 Auditor's.

### D34. Lazy ingestion on first request
- **Alternatives:** pre-ingest a fixed list of tickers; ingest in a background job.
- **Why:** any ticker works without setup.
  - The first run for a ticker downloads and indexes its 10-K (a few seconds once the embedding model is cached), and later runs reuse the index.
  - Filings are optional: if none is found, the debate continues on market data only and a warning is logged.
- **Trade-off:** the first run for each ticker is slower, and an indexed filing is not refreshed when a newer 10-K is filed (Stage 8: compare against EDGAR's latest accession).

### D35. Fit within Groq's free-tier tokens-per-minute limit
- **Alternatives:** fewer excerpts; a paid tier; the smaller model for the agents.
- **Why:** measured live, one agent call used about 4,200 prompt tokens and 1,300 completion tokens. The limit for `gpt-oss-120b` is 8,000 tokens per minute, and the first RAG debate failed after 3 attempts. Three fixes:
  - market data is sent as compact JSON with a roughly monthly price history, saving about 800 tokens (the full data stays in state);
  - the `retry-after` cap went from 30s to 60s, because the observed reset was about 48s;
  - the default number of attempts went from 3 to 4.
- **Trade-off:** a 2-round debate takes about 2 minutes on the free tier, and the agents see a coarser price history.
