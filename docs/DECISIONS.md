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

## Stage 5

### D36. The Auditor does not see the Critic's feedback
- **Alternatives:** give the auditor the whole debate transcript.
- **Why:** the auditor's job is grounding, i.e. checking each claim against the evidence. If it reads the critic's objections it tends to repeat them (anchoring), and then it is not an independent check. It gets exactly what the analyst had: the market data, the excerpts, and which ids were cited.
- **Trade-off:** it can re-discover problems the critic already raised (one extra LLM call's worth of work), and it can't use the debate to understand intent.

### D37. Verdict consistency check (`is_grounded` must match `unsupported_claims`)
- **Alternatives:** trust the flag; derive `is_grounded` in code from whether the list is empty.
- **Why:** a contradictory verdict ("grounded" with listed problems, or "ungrounded" with none) is ambiguous for the UI and the evals. The check reuses the `extra_check` hook, so it gets the normal single repair attempt. We keep both fields because the spec defines both, and requiring the model to state them separately makes it commit explicitly.
- **Trade-off:** occasionally an extra LLM call.

### D38. Frozen evidence fixtures for judge cases
- **Alternatives:** fetch live data when the evals run.
- **Why:** a judge label such as "trailing P/E is 38.6, so 45.2 is wrong" is only true for one snapshot. Live data would silently invalidate labels as prices move. `capture_fixture.py` can re-capture the snapshot, and a test pins the numbers the labels depend on, so re-capturing without re-labelling fails loudly.
- **Trade-off:** the fixture ages; the judge is tested on a 10-K and prices that are not the latest. That's acceptable, because grounding doesn't depend on recency.

### D39. Judge labels that hold by construction (planted flaws)
- **Alternatives:** label real pipeline outputs by hand; ask a stronger LLM to label.
- **Why:** each judge case starts from a fully grounded analysis and plants exactly one known flaw (wrong number, unit, period, unsupported citation, outside knowledge). That makes the label unambiguous and lets us check claim-level recall (did the auditor name *that* claim?). Two grounded controls (full and opinion-only) test for false alarms. Labelling real outputs would be more realistic but slow and subjective; LLM labels would be circular.
- **Trade-off:**
  - planted flaws are cleaner than real mistakes;
  - 7 cases is a small sample, so scores move a lot with one case;
  - the labels were verified by the developer, and the owner should spot-check them (each case's `notes` field explains its label).

### D40. Precision/recall with "ungrounded" as the positive class
- **Alternatives:** plain accuracy; "grounded" as the positive class.
- **Why:** the auditor exists to catch problems, so "flagged as ungrounded" is the event we care about. Recall answers "how many real problems does it catch?", and precision answers "how often is a flag a false alarm?". Accuracy hides the trade-off: a judge that always says "grounded" scores 2/7 here while catching nothing.
- **Trade-off:** with only 2 grounded controls, precision rests on very few false-alarm opportunities.

## Stage 6

### D41. The graph runs in a background worker; the stream replays persisted events
- **Alternatives:**
  - run the graph inside the SSE request;
  - a separate task queue (Celery/RQ with Redis).
- **Why:**
  - A debate takes 1–4 minutes. Inside the request, a closed tab or a dropped connection would kill the run and waste its tokens, and nothing could resume it.
  - With a worker that commits each step to `run_events`, the stream is just a reader. Reconnects resume via `Last-Event-ID`, history needs no extra code, and the same events feed the UI and the DB.
  - An in-process `ThreadPoolExecutor` needs no extra infrastructure on a free hosting tier.
- **Trade-off:**
  - Runs in progress are lost on restart. They are marked failed at startup, not resumed.
  - This only works on a single API instance. Scaling out needs a real queue (Stage 8/9).
  - There is a global limit of 2 concurrent runs.

### D42. SSE authenticated with the Authorization header (frontend uses fetch, not EventSource)
- **Alternatives:**
  - the token in a query string (`?token=`), which `EventSource` supports;
  - a session cookie.
- **Why:**
  - Tokens in URLs end up in server and proxy logs and in browser history.
  - Cookies would bring CSRF handling and server sessions back, and D5 chose stateless Bearer tokens.
  - `fetch()` with a streaming body reader can send headers and parse SSE in a few lines (Stage 7).
- **Trade-off:** the frontend can't use the built-in `EventSource`, with its automatic reconnect. We implement reconnect ourselves with `Last-Event-ID`.

### D43. Quota counted from `research_runs`; checked before any LLM call
- **Alternatives:** a separate quota/usage table; Redis counters; token-based budgets.
- **Why:**
  - Runs are the expensive unit, and the table already has `user_id`, `status` and `created_at`, so no extra state is needed.
  - Limits: 1 active run and 5 runs per rolling 24 hours.
  - The check runs before ticker extraction, and a failed extraction creates no run, so a typo costs nothing.
  - Failed runs still count, because they used LLM calls.
- **Trade-off:**
  - The check and the insert are not atomic, so two simultaneous requests from the same user could both pass the active-run check. That's acceptable at this scale; a row lock or unique partial index would fix it on Postgres (Stage 8).
  - Limits are per run, not per token.

### D44. Another user's run id returns 404, not 403
- **Alternatives:** 403 Forbidden.
- **Why:** 403 confirms that the id exists. With 404 the API reveals nothing about other users' runs. The UUID ids (D5) already make guessing impractical; this is defence in depth.
- **Trade-off:** slightly harder debugging ("does it exist?") during development.

### D45. `create_app()` takes its collaborators as parameters
- **Alternatives:** a module-level app with `app.dependency_overrides` / monkeypatching in tests.
- **Why:** the session factory, token verifier, run worker, ticker extractor and stream timings are passed in and stored on `app.state`. Tests build an app with an in-memory DB, a fake verifier, a fake graph and an inline executor, with no global patching. Production uses `app = create_app()` with the real ones.
- **Trade-off:** dependencies read from `request.app.state` instead of plain imports, which is a small indirection.

### D46. `run_events.step` extended beyond analyst/critic/auditor
- **Alternatives:** keep the 3 original steps and send other progress as transient SSE messages.
- **Why:** the stream replays only what is in the DB (D41). The UI needs to show "fetching data", round boundaries (`consensus_check`, which also carries `forced_exit`) and failures (`error`). Storing them makes replay and history complete. CLAUDE.md's spec was updated.
- **Trade-off:** more rows per run (about 3 + 3 per round); `fetch_data` stores only a summary to keep rows small.

### D47. SSE implemented by polling the DB from a sync generator
- **Alternatives:** `sse-starlette`; an async generator with an in-process pub/sub queue; Postgres LISTEN/NOTIFY.
- **Why:**
  - The simplest correct design: every 0.5s, read the status and the new events, send them, and stop when the run is finished.
  - It works the same for a live run, a finished run and a resumed connection, and on SQLite and Postgres.
  - A heartbeat comment every 15s keeps proxies from closing idle streams, and a 30-minute cap ends streams for stuck runs.
- **Trade-off:**
  - up to 0.5s of latency per event;
  - one thread and one cheap query per poll for each open stream;
  - fine for a handful of users, while pub/sub would scale further.

## Stage 7

### D48. TypeScript for the frontend (owner's choice)
- **Alternatives:** plain JavaScript.
- **Why:** the UI is mostly rendering backend JSON (events, verdicts, final output). `api/types.ts` mirrors those shapes once, as a discriminated union over `step`. A backend/frontend mismatch then fails `tsc` at build time instead of showing a blank card at runtime.
- **Trade-off:** more syntax to read and explain. The types have to be updated by hand when the backend changes, because nothing generates them from OpenAPI.

### D49. Google Identity Services used directly, no wrapper library
- **Alternatives:** `@react-oauth/google`; Firebase Auth; a full OAuth redirect flow.
- **Why:**
  - The whole integration is about 60 lines: load the script once, `initialize({client_id, callback})`, `renderButton`, and `disableAutoSelect` on sign-out.
  - One fewer dependency to trust, and every line can be explained.
  - The ID token from the callback is exactly what the backend verifies (D5).
- **Trade-off:** we maintain the small loader and its types ourselves, and there is no One Tap prompt or automatic silent re-sign-in.

### D50. Token in memory + `sessionStorage`; decoded only for display and expiry
- **Alternatives:** memory only (re-sign-in on every reload); `localStorage`; an httpOnly cookie set by the backend.
- **Why:**
  - `sessionStorage` survives a reload but is scoped to one tab and cleared when it closes. That is a smaller theft window than `localStorage`, and keeps the stateless Bearer design (no cookies, so no CSRF handling).
  - The frontend never trusts the token's contents for security.
  - Auto sign-out about a minute before `exp` avoids confusing failures with an expired token.
- **Trade-off:**
  - Like any token readable by JavaScript, it is exposed to XSS, so a strict CSP is a Stage 8 hardening item.
  - Users must sign in again after about an hour, because there are no refresh tokens.

### D51. fetch-based SSE client with `Last-Event-ID` reconnect, plus an id-deduplicating reducer
- **Alternatives:** `EventSource` (cannot send the Authorization header, D42); a library such as `@microsoft/fetch-event-source`.
- **Why:**
  - The SSE format is simple (blank-line-separated `field: value` lines), so a tested 40-line parser beats a dependency.
  - Reconnecting with `Last-Event-ID` resumes exactly where the stream stopped.
  - The reducer ignores event ids at or below the last applied id, so a replay can never double a round.
  - Live, resumed and history views share one code path.
- **Trade-off:** our own parser must follow the SSE spec. It covers the subset our server emits (id, event, data, comments) and is unit-tested for chunk splitting and CRLF line endings.

### D52. Citation excerpt text loaded after the run, not streamed
- **Alternatives:** include all 8 excerpt texts in the `fetch_data` event.
- **Why:** citation ids are known live, and the texts are already in `final_output.filing_chunks`. The run page fetches them once the stream ends, so the backend is unchanged and every `fetch_data` row stays small (D46). An already-opened chip fills in automatically when the text arrives.
- **Trade-off:** while a run is live, clicking a citation shows "shown once the run has finished" instead of the text.

### D53. Plain CSS and `react-router-dom`
- **Alternatives:** Tailwind or a component library; state-based navigation without a router.
- **Why:**
  - One small stylesheet with CSS variables, a dark-mode override and a reduced-motion rule is enough for 4 screens, and adds no build tooling.
  - The router gives each run a real URL (`/runs/:id`), so reloads, history links and sharing a run with yourself all work.
- **Trade-off:** no ready-made component styling; the visual design is deliberately simple.

## Stage 8: Deployment + hardening

### D54. Neon + Render + Vercel, all on free tiers
- **Alternatives:** Supabase (database); Railway or Fly.io (backend); Netlify (frontend).
- **Why:**
  - **Render** runs our Dockerfile from a `render.yaml` Blueprint, so the service is defined in code.
  - **Neon** gives managed Postgres 17 near Render's Oregon region.
  - **Vercel** builds the Vite app and applies the headers in `vercel.json`.
  - All three sign in with GitHub and need no card.
- **Trade-offs:**
  - Render's free instance has 512 MB of RAM and about 0.1 CPU.
  - It sleeps after 15 minutes idle; the first request after that waits about a minute.
  - Its disk is wiped on every deploy and sleep, so the ChromaDB index and downloaded filings don't persist.
  - The API therefore runs `MAX_CONCURRENT_RUNS=1`.

### D55. Postgres schema only through Alembic migrations
- **Alternatives:** keep `create_all` everywhere.
- **Why:**
  - `alembic upgrade head` runs before Uvicorn starts in the container, so the deployed schema is always the migrated one, and future column changes have a path.
  - SQLite (local and tests) still uses `create_all`, for speed.
  - CI runs the migrations up, down and up again on SQLite and Postgres, and checks that the models and migrations haven't drifted apart.
- **Trade-off:** every model change now needs a migration file.

### D56. Atomic quota: row lock on the user, then re-check
- **Alternatives:** a unique partial index on active runs; Postgres advisory locks; accepting the race.
- **Why:**
  - `POST /research` checks the quota quickly before the slow LLM ticker extraction, then commits.
  - It then locks the user row (`SELECT ... FOR UPDATE`), checks again, and inserts the run in the same transaction.
  - Two simultaneous requests from the same user take turns; other users are never blocked.
  - The Postgres CI test with `threading.Barrier` showed the race without the lock.
- **Trade-off:** SQLite ignores `FOR UPDATE`. Local development keeps the (harmless) race.

### D57. Production refuses to start when misconfigured
- **Alternatives:** fail on the first request; warn in the logs.
- **Why:**
  - With `APP_ENV=production`, the app (and the migrations) stop at startup and list what's wrong:
    - a missing setting;
    - a SQLite `DATABASE_URL`;
    - a malformed or `http://` origin;
    - `EMBEDDING_PROVIDER=cohere` without a key.
  - The messages name settings, never their values.
  - Settings are trimmed of pasted whitespace (`env_str`).
  - The allowed CORS origins are logged at startup (they're public URLs).
  - Every one of these came from a live problem (see DEVLOG).
- **Trade-off:** a typo stops a deploy instead of degrading quietly. That's intended; Render keeps the previous version serving.

### D58. Time limits at every level
- **Alternatives:** one overall timeout; killing threads.
- **Why:** Python threads can't be killed safely, so each layer has its own bound:
  - **Groq:** 60 s per request, up to 4 attempts.
  - **Filing ingestion:** `fetch_data` waits at most 120 s, then continues without 10-K excerpts. The ingestion finishes in the background and is cached.
  - **Run timeout:** 600 s, checked between steps.
  - **Run expiry:** a run still active 15 minutes after creation is marked failed for that user. This happens before the quota check and on `/me`, so a stuck run can never block a user. The worker notices the expiry and stops.
- **Trade-off:** a run can overrun by up to one step before it is stopped.

### D59. Ticker extraction may decline (`no_us_listing`)
- **Alternatives:** keep forcing a ticker and rely on "no market data".
- **Why:** forcing a ticker made the model invent one (Reliance → `RIL`). The schema now allows `{"no_us_listing": true}`, which returns a clear 422 without a repair attempt or any quota use.
- **Trade-off:** the model could decline a company that is listed. The live extraction evals still pass 4/4.

### D60. Verify the Docker image in GitHub Actions
- **Alternatives:** local Docker, which wasn't available because Docker Desktop's WSL VM was missing and BIOS virtualization couldn't be changed.
- **Why:** CI builds the image and runs it the way production does:
  - against a Postgres 17 service;
  - as a non-root user;
  - with migrations run at startup;
  - checking `/health`, the 401 response and the security headers;
  - checking that production refuses to start without secrets;
  - running an in-container memory probe on a real ingestion path.
  - Every job uses `shell: bash`, so `pipefail` is on.
- **Trade-off:** each image check needs a push and costs about 5 minutes. Two checks that couldn't fail were found and fixed (DEVLOG).

### D61. Embed in batches of 8 for memory
- **Alternatives:** the library's internal batch of 32; a smaller model.
- **Why:** measured peak memory by batch size: 8 → ~375 MB, 16 → ~440 MB, 32 → ~575 MB. The first 10-K ingestion had pushed a 512 MB instance over its limit (625 MB in CI).
- **Trade-off:** about 30% slower local embedding. Quality is unchanged, because batching doesn't change the vectors.

### D62. Hosted embeddings in production: Cohere (`embed-english-v3.0`)
- **Alternatives:**
  - the local model, which was measured too slow: about 3.5 minutes per 10-K on a laptop and an estimated 30+ minutes on Render's 0.1 CPU;
  - Gemini's free tier, which was measured too slow: 586 s per 10-K, because every text counts against a 100/min quota;
  - pre-indexing a few tickers into the image;
  - only the 120 s fallback, which would mean almost no 10-K evidence on the deployed site.
- **Why:**
  - Cohere takes 96 texts per call (2,000/min), with separate document and query input types.
  - Measured first-time ingestion: MSFT 27 s, AAPL 29 s.
  - `EMBEDDING_PROVIDER=local` stays the default, so development, tests, CI and evals need no key.
  - Each model gets its own Chroma collection, because the vector sizes differ (384 vs 1024).
  - Embedding errors degrade to "no excerpts" instead of failing the run.
- **Trade-offs:**
  - filing text is sent to a third party;
  - the trial key allows 1,000 calls a month, roughly 100 first-time 10-Ks;
  - local and deployed retrieval use different models, so their results can differ slightly.

### D63. Errors stay readable at the edges
- **Alternatives:** rely on the framework defaults.
- **Why:** three live failures came down to errors that were reported as something else:
  1. **Groq rejecting the key:** a raw `groq.AuthenticationError` escaped every `except LLMError` and crashed the request.
     - Now 401/403 become `LLMConfigError`, and other non-retryable statuses become `LLMError`, which the API answers with a handled 502.
  2. **Crashes looked like CORS errors:** Starlette builds its 500 outside `CORSMiddleware`, so the browser reported a CORS failure instead of a server error.
     - `UnhandledErrorMiddleware` now sits inside CORS and returns a JSON 500 that carries the CORS headers.
  3. **Idle transactions:** a request session held a transaction open for a whole SSE stream, and Neon terminated it.
     - Reads now commit as soon as they're done (`expire_on_commit=False` keeps objects usable).
- **Trade-off:** more code at the boundaries. Each piece has a test that was shown to fail without it.

## Stage 9: Extensions

### D64. Per-run metrics through context variables
- **Alternatives:**
  - passing a metrics object through every function;
  - LangSmith or OpenTelemetry;
  - parsing the logs afterwards.
- **Why:**
  - The worker opens a `RunMetrics` collector and makes it "current" with a `ContextVar`. The Groq client, embedders and ingestion report to it when one is active, so `core/` stays user- and run-agnostic and no signatures change.
  - LangGraph runs nodes with a copy of the caller's context, and `fetch_data` submits ingestion with `copy_context()`. A test proves both see the collector.
  - Metrics are numbers only: step durations, LLM calls, retries, tokens, embedding calls and ingestion timing. They are saved for failed runs too, which still cost tokens.
  - The same context adds ` [run=<id8>]` to log lines.
- **Trade-off:** context variables are implicit. Code outside a run (CLI, evals) records nothing, by design. No external tracing service, so there are no dashboards across runs.

### D65. Risk Officer as a separate LLM role after the debate
- **Alternatives:**
  - make the critic list risks;
  - a parallel "bear" analyst during the debate.
- **Why:**
  - The critic's job is to challenge the thesis until it holds up; the risk officer's is to name what could still make the final thesis wrong.
  - It runs once, after consensus or a forced exit, so it costs one extra call (about 30–40 s on the free tier).
  - It sees the unresolved objections when there was no consensus.
  - Its citations are validated like the analyst's.
- **Trade-off:** its output is not audited; the Auditor still checks only the analyst's thesis. For a bearish thesis, its "risks" are reasons the thesis could be too pessimistic. That is intended, but can surprise a reader.

### D66. Data Checker in code, shown next to the Auditor, not fed into it
- **Alternatives:**
  - feed code-verified numbers into the Auditor prompt;
  - leave numeric checks to the LLM.
- **Why:**
  - Stage 5 showed that the Auditor's false alarms come from its own arithmetic and unit mistakes.
  - A deterministic lookup of `field = value` data points is cheap and exact.
  - Keeping it out of the prompt keeps judge calibration (D33–D35) comparable.
  - It is strict where scale errors happen: `_pct` fields are already percentages, and only true fraction fields may be written as `%`. Each written reading has its own rounding tolerance.
- **Trade-off:** it can't check prose claims, derived numbers or periods. About 1 in 10 data points in a live AAPL run was "not checkable".

### D67. Comparison = the same graph per ticker, then one Comparator call
- **Alternatives:**
  - one big multi-ticker graph;
  - running tickers in parallel;
  - a comparator that sees every excerpt.
- **Why:**
  - Reusing the single-ticker graph means every company gets the same debate, risk check and audit, with no new loop logic.
  - Tickers run one after another, because Groq's free tier allows about 8k tokens a minute and parallel runs would only wait on rate limits.
  - Each company gets 1 round, to bound time and cost.
  - The Comparator sees compact briefs: no price history, and only excerpts someone cited, shortened to 400 characters. This keeps a 3-company prompt well under the per-minute budget.
- **Trade-offs:**
  - about 3–4 minutes per company;
  - the comparison itself isn't audited, and the UI says so;
  - a live run produced a wrong derived number (see DEVLOG), so the prompt now requires the inputs of any derived number.

### D68. One run row per comparison, costing one quota unit per ticker
- **Alternatives:** one run row per ticker grouped by a parent id; a comparison costing 1.
- **Why:**
  - One row keeps streaming, history, expiry and the worker unchanged. `kind`, `tickers` and `units` columns describe it.
  - The daily quota sums `units`.
  - The quota check runs before extraction (minimum 2) and again with the real N under the row lock (D56).
  - Run timeout and stuck-run expiry scale with N.
- **Trade-off:** per-ticker results live inside `final_output.per_ticker` rather than in their own rows, so they can't be queried individually.

### D69. Comparison events are tagged, and the UI reuses the single-run reducer
- **Alternatives:** a separate event schema and renderer for comparisons.
- **Why:**
  - The worker adds `ticker` to each per-company event's content.
  - `compareReducer` routes events by that tag into the existing `runReducer`, so each company tab renders exactly like a single run, including replay and reconnect.
  - The "Comparison" verdict is a separate card.
- **Trade-off:** event content is no longer purely what the graph returned; the tag is added by the API layer, not by `core/`.

### D70. Comparison extraction must decline foreign companies as a whole
- **Alternatives:** drop the foreign company and compare the rest.
- **Why:** silently comparing a different set than the user asked for is worse than a clear 422. It uses the same `no_us_listing` escape hatch as single runs (D59). Duplicates are removed, and fewer than 2 distinct tickers triggers the repair attempt.
- **Trade-off:** the user must rephrase without the foreign company.
