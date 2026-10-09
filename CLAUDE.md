# Project: Automated Financial Research & Multi-Agent Consensus Network

## Overview
Multi-user, deployed web app. A user signs in with Google, submits a stock ticker, and watches an Analyst agent and a Critic agent debate (a LangGraph loop with an iteration guardrail). An Auditor agent (LLM-as-a-Judge) then checks the final claims against retrieved SEC filing text. Output streams live to a React UI. Financial reasoning is 100% delegated to the LLM via prompts; our code only plumbs data in and structured output out.

## Environment
- Owner works on Windows in the Cursor IDE terminal (PowerShell). Give PowerShell commands, not bash (no `touch`, `source`, `export`). Activate venv with `backend\venv\Scripts\activate`.
- The project folder is `financial-consensus-agent`.

## Tech stack
- Backend: Python 3.11+, FastAPI, Uvicorn, SQLAlchemy
- AI: LangGraph, Groq SDK (`openai/gpt-oss-20b` for tool calling, `openai/gpt-oss-120b` for reasoning; Groq retired its Llama chat models, see DECISIONS D23), Pydantic v2
- Data: yfinance, SEC filings (PDF parsing), ChromaDB (vector store)
- Auth: Google OAuth 2.0 (Google Identity Services on the frontend, ID token verified in FastAPI with `google-auth`)
- DB: SQLite locally, Postgres (Supabase/Neon free tier) when deployed
- Frontend: React (Vite), SSE streaming from the backend
- Deploy: backend on Render/Railway/Fly.io, frontend on Vercel/Netlify

## Directory structure
```
financial-consensus-agent/
├── CLAUDE.md
├── README.md
├── LICENSE
├── .gitignore
├── backend/
│   ├── .env.example            # GROQ_API_KEY, GOOGLE_CLIENT_ID, DATABASE_URL, FRONTEND_ORIGIN
│   ├── requirements.txt
│   ├── src/
│   │   ├── main.py             # FastAPI app, CORS, router registration
│   │   ├── config.py           # env loading, model names, MAX_ITERATIONS
│   │   ├── core/               # PURE AI LOGIC: knows nothing about users
│   │   │   ├── state.py        # GraphState
│   │   │   ├── schemas.py      # AnalystOutput, CriticFeedback, AuditorVerdict, tool-call schemas
│   │   │   ├── graph.py        # StateGraph build + compile
│   │   │   ├── nodes/
│   │   │   │   ├── fetch_data.py
│   │   │   │   ├── analyst.py
│   │   │   │   ├── critic.py
│   │   │   │   ├── consensus_check.py   # guardrail routing function
│   │   │   │   └── auditor.py
│   │   │   └── prompts.py      # all system prompts (finance reasoning lives here)
│   │   ├── tools/
│   │   │   └── market_data.py  # yfinance wrappers
│   │   ├── clients/
│   │   │   └── groq_client.py  # Groq wrapper with retry/backoff
│   │   ├── rag/
│   │   │   ├── ingest.py       # parse SEC PDFs, semantic chunking
│   │   │   └── retriever.py    # ChromaDB + metadata pre-filtering
│   │   ├── api/                # INFRA LAYER: user-aware
│   │   │   ├── auth.py         # verify Google ID token -> user_id (Google `sub`)
│   │   │   ├── routes_research.py   # POST /research, GET /research/{id}/stream (SSE), GET /history
│   │   │   └── quota.py        # per-user rate/cost limits, checked BEFORE invoking the graph
│   │   └── db/
│   │       ├── models.py       # users, research_runs, run_events
│   │       └── session.py
│   ├── evals/                  # standalone eval harness, NOT part of the live pipeline
│   │   ├── golden_set.json     # 10-15 hand-verified cases
│   │   ├── run_evals.py        # score pipeline vs golden answers
│   │   └── judge_calibration.py  # compare Auditor verdicts vs manual labels (precision/recall)
│   └── tests/
│       ├── test_schemas.py
│       ├── test_nodes.py
│       └── test_guardrail.py
├── docs/
│   ├── DESIGN.md
│   ├── DECISIONS.md
│   ├── PROGRESS.md
│   ├── DEVLOG.md
│   └── INTERVIEW_PROGRESS.md
└── frontend/                   # added at Stage 7
    └── (Vite + React: login page, ticker input, live debate view, history page)
```

## Core design
**Separation of concerns:** `core/` takes a ticker and returns a final GraphState. It never sees `user_id`. `api/` handles auth, quota, persistence, and streaming around it. Per-user config (for example model tier) is passed into GraphState as a parameter by the API layer.

**Graph:** `fetch_data -> analyst -> critic -> consensus_check`
- `max_iterations` is the maximum number of Analyst rounds IN TOTAL (default 3 = at most 3 analyst + 3 critic runs). `iteration_count` = completed rounds.
- After each critic step, `consensus_check` increments `iteration_count`, then returns `"done"` if `critic_feedback.agrees` is true, or if `iteration_count >= max_iterations` (forced exit: `forced_exit=True`; still goes to the Auditor and is flagged as unresolved disagreement).
- Otherwise it returns `"continue"`, looping back to `analyst`. (Implemented as a node that updates state + a pure router; see DECISIONS D24/D27.)
- `done -> auditor -> END`

**Schemas (Pydantic v2):**
- `AnalystOutput`: thesis (str), supporting_data_points (list[str]), reasoning (str), confidence (Literal low/medium/high), filing_citations (list[str], default [])
- `CriticFeedback`: agrees (bool), objections (list[str]), missing_context (list[str])
- `AuditorVerdict`: is_grounded (bool), unsupported_claims (list[str]), notes (str)
- `GraphState`: ticker, raw_data (dict), filing_chunks (list[str]), analyst_output | None, critic_feedback | None, iteration_count (int, default 0), max_iterations (int, default 3), auditor_verdict | None, forced_exit (bool, default False)
- Tool-call input schema for market data: ticker validated as uppercase, non-empty, no exchange suffix

**DB tables:**
- `users`: id (Google sub, PK), email, created_at
- `research_runs`: id, user_id (FK), ticker, status (pending/running/done/failed), created_at, final_output (JSON)
- `run_events`: id, run_id (FK), step (fetch_data/analyst/critic/consensus_check/auditor/error), content (JSON), timestamp
- Every query is scoped by user_id; no cross-user reads.

## Stage plan (do not skip ahead; each stage must run cleanly and pass its tests before the next)
0. Design: write `docs/DESIGN.md` (scope, Mermaid graph diagram, auth decision, core/ vs api/ separation), then implement schemas, state and DB models exactly as specified above
1. Data ingestion: yfinance wrapper, raw Python objects, no LLM
2. Tool calling + structured extraction: single Groq call with a Pydantic tool schema, reject malformed args
3. LangGraph Analyst/Critic loop with a real iteration-cap guardrail, plus tests for the forced-exit case
4. RAG over SEC filings: semantic chunking, metadata pre-filtering, ChromaDB, retrieval wired into the Analyst
5. Auditor (LLM-as-a-Judge) + standalone eval harness in `backend/evals/`: golden dataset, scored runs, judge calibration against manual labels
6. FastAPI backend: Google token auth, quota check, persistence, SSE streaming
7. React frontend: login, ticker submit, live Analyst/Critic/Auditor stream, per-user history
8. Deployment + hardening: hosted DB, secrets, Groq rate-limit/backoff handling, bad-ticker and timeout handling
9. Extensions: multi-ticker comparison, extra agent roles, observability

## Build mode (the owner learns AFTER the build, not during it)

### While building
- Build the complete project from scratch, stage by stage, in the order above. Implement everything yourself; I do not write code. Pause between stages for plan approval.
- Finish and verify each stage (run it, run its tests) before starting the next.
- Do not stop for explanations or quizzes. Keep chat output short: what you built, how you verified it, what's next.
- At the start of each stage, present a plan before writing code: files to create or change, order of work, tests, and git steps (branch name,       commit messages, tag). Wait for my approval. Once approved, build the whole stage without further check-ins.
- Save each approved plan in docs/PROGRESS.md under that stage's heading.
- Ask me only for things you cannot do yourself: API keys, Google OAuth client ID, deployment account logins.
- Write clean, readable, commented code with type hints. Prefer simple code over clever code, because I will have to explain it later.
- Write real tests for the guardrail (forced exit at max_iterations), schema validation, and user-scoped queries.
- Build the Stage 5 eval harness in `backend/evals/` as a standalone artifact, separate from the live Auditor node.

### Records to keep while building (for the teaching phase later)
- `docs/DEVLOG.md`: every bug, failure or wrong LLM output you hit, with symptom, root cause, fix, and prevention. Write it honestly; it is the source for my "tell me about a bug" answers.
- `docs/DECISIONS.md`: each design choice, the alternatives, and the trade-off.
- `docs/PROGRESS.md`: update at the end of each stage with what was built, files touched, and how it was tested. If a session stops midway, this file is how work resumes.

### Teaching mode (ONLY when I say "teach me stage N" or "teach me the project")
Do nothing teaching-related until I ask. When I do:
1. Walk me through that stage using the real code in this repo: the concept, why it's built this way, how the files connect, and the matching DEVLOG failures.
2. Then ask me 5 mock-interview questions one at a time (2 concept, 1 walk-through of my code, 1 bug from DEVLOG, 1 curveball), grade each answer, and give a stronger version.
3. Keep a score in `docs/INTERVIEW_PROGRESS.md` and re-ask weak answers.
4. At the end of the full project, produce `docs/INTERVIEW_CHEATSHEET.md` (60-second pitch, failures in STAR format, design trade-offs, honest limits).

## Git & GitHub rules

### Setup (do this at the very start, before any other code)
1. Run `git init` in the repo root and set the default branch to `main`.
2. Create `.gitignore` FIRST, before the first `git add`, with at least:
```
# Secrets (NEVER commit)
.env
.env.*
!.env.example
*.pem
*.key
*credentials*.json
client_secret*.json
service-account*.json
secrets/

# Python
venv/
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/

# Local data and databases
*.db
*.sqlite
*.sqlite3
chroma_db/
.chroma/
**/data/raw/
**/data/filings/
*.pdf

# Node / frontend
node_modules/
dist/
build/
.vite/

# Editor / OS
.vscode/
.idea/
.DS_Store
Thumbs.db

# Claude Code local files
.claude/settings.local.json

# Personal study notes (not for the public repo)
docs/DEVLOG.md
docs/INTERVIEW_PROGRESS.md
docs/INTERVIEW_CHEATSHEET.md
```
3. Create the GitHub repo with the GitHub CLI as **private** first: `gh repo create financial-consensus-agent --private --source=. --remote=origin`. Only make it public when I explicitly say so.
4. If `gh` is not installed or not logged in, tell me the exact commands (`winget install GitHub.cli`, then `gh auth login`) and wait.

### Never commit or push
- API keys, tokens, OAuth client secrets, `.env` files, database files, vector store folders, downloaded SEC PDFs, `venv/`, `node_modules/`.
- Only `.env.example` is committed, with placeholder values like `GROQ_API_KEY=your_key_here`.
- Before EVERY commit run `git status` and `git diff --staged`, and check that no secret or large data file is staged. Never use `git add .` blindly; stage specific paths or review first.
- Before the first push, and before making the repo public, scan the whole history for secrets (for example `git log -p | findstr /i "gsk_ api_key secret token"`). If anything leaked, stop and tell me; the key must be revoked and regenerated, because deleting it in a later commit does not remove it from history.
- Never use `git push --force` and never rewrite pushed history unless I ask.

### Commit conventions
- Commit at the end of each logical unit of work (a stage, or a meaningful piece inside a stage), only when its tests pass.
- Use Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`.
  Example: `feat(core): add consensus_check guardrail with max_iterations cap`
- Subject line under 72 characters, imperative mood. The body explains WHY when the change is non-obvious.
- One concern per commit. Do not mix unrelated changes.
- Tag the end of each stage (`stage-0-design`, `stage-1-ingestion`, and so on) and push the tags.

### Branching
- `main` always works. Do each stage on a branch `stage/N-short-name`, merge into `main` when the stage is verified, then tag.

### Documentation in the repo
- `README.md`: one-paragraph pitch, architecture diagram (Mermaid), tech stack, setup instructions (Windows and Mac/Linux), env variables table, how to run backend/frontend/tests/evals, project status by stage, and honest limitations.
- `docs/DESIGN.md`, `docs/DECISIONS.md`, `docs/PROGRESS.md` are committed. The personal study notes listed in `.gitignore` stay local unless I say otherwise.
- Keep `.env.example` and the `LICENSE` (MIT) up to date.

### Push
- Push `main` and tags after each stage is merged: `git push origin main --tags`.
- After pushing, show me `git log --oneline -10` and the repo URL.