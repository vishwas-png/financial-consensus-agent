"""Central configuration: loads environment variables once and exposes constants.

Secrets (API keys, client IDs) are read from the environment / backend/.env and
are never hardcoded here.
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

# backend/ directory (this file lives in backend/src/config.py)
BACKEND_DIR = Path(__file__).resolve().parent.parent

# Load backend/.env if it exists. Real environment variables take precedence.
load_dotenv(BACKEND_DIR / ".env")

def env_str(name: str, default: str | None = None) -> str | None:
    """Read a setting with surrounding whitespace removed ("" counts as unset).

    Values pasted into a hosting dashboard can carry an invisible trailing newline. In an API
    key that makes the HTTP header invalid, and the SDK reports a confusing "Connection error".
    """
    value = os.getenv(name, "").strip()
    return value or default


# --- Environment ---
# "production" makes the app refuse to start if a required setting is missing (see main.py).
APP_ENV: str = env_str("APP_ENV") or "development"
IS_PRODUCTION: bool = APP_ENV == "production"

# --- Secrets / deployment settings ---
GROQ_API_KEY: str | None = env_str("GROQ_API_KEY")
GOOGLE_CLIENT_ID: str | None = env_str("GOOGLE_CLIENT_ID")


def parse_origins(value: str) -> list[str]:
    """'https://a.app, http://localhost:5173/' -> ['https://a.app', 'http://localhost:5173']"""
    return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]


# Browser origins allowed to call the API (comma-separated, e.g. the Vercel URL + localhost)
FRONTEND_ORIGINS: list[str] = parse_origins(os.getenv("FRONTEND_ORIGIN", "http://localhost:5173"))
FRONTEND_ORIGIN: str = FRONTEND_ORIGINS[0] if FRONTEND_ORIGINS else "http://localhost:5173"

# A browser origin is exactly scheme://host[:port]: no path, quotes, spaces or wildcards
ORIGIN_PATTERN = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")


def normalize_database_url(url: str) -> str:
    """Make hosted Postgres URLs use the psycopg (v3) driver.

    Neon/Render/Heroku hand out "postgres://..." or "postgresql://..."; SQLAlchemy would
    otherwise pick the psycopg2 driver, which we don't install.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


# Default to a local SQLite file inside backend/. In deployment this points to Postgres.
DATABASE_URL: str = normalize_database_url(env_str("DATABASE_URL") or f"sqlite:///{BACKEND_DIR / 'app.db'}")

# --- Model names (Groq) ---
# Groq retired its Llama chat models (see DECISIONS D11/D23); both are open-weight gpt-oss models.
TOOL_CALL_MODEL: str = "openai/gpt-oss-20b"    # smaller + faster, used for tool calling / extraction
REASONING_MODEL: str = "openai/gpt-oss-120b"   # larger model for analyst/critic/auditor reasoning

# --- SEC filings / RAG (Stage 4) ---
# SEC requires a User-Agent with a name and contact email on every EDGAR request.
SEC_USER_AGENT: str | None = env_str("SEC_USER_AGENT")
FILINGS_DIR = BACKEND_DIR / "data" / "filings"  # downloaded filings + manually added PDFs (gitignored)
CHROMA_DIR = BACKEND_DIR / "chroma_db"           # persistent vector store (gitignored)
TOP_K_CHUNKS: int = 8                            # filing excerpts given to the agents per run

# Embeddings: "local" runs all-MiniLM-L6-v2 on this machine (dev, tests, CI). "cohere" calls
# Cohere's embedding API: the deployed free instance has ~0.1 CPU, where embedding one 10-K
# locally took 30+ minutes (~3.5 min on a laptop). Gemini's free tier was measured too slow
# (100 texts/min: ~10 min per 10-K). See DECISIONS.
EMBEDDING_PROVIDER: str = (env_str("EMBEDDING_PROVIDER") or "local").lower()
COHERE_API_KEY: str | None = env_str("COHERE_API_KEY")
COHERE_EMBEDDING_MODEL: str = "embed-english-v3.0"   # 1024-dim English retrieval model
# Longest a run waits for filing ingestion before continuing without 10-K excerpts
FILING_TIMEOUT_SECONDS: int = 120

# --- Graph guardrail ---
# Hard cap on Analyst <-> Critic rounds before the graph is forced to exit.
MAX_ITERATIONS: int = 3

# --- API quotas (Stage 6), checked BEFORE any graph work ---
RUNS_PER_DAY: int = 5                # research runs per user per rolling 24 hours
MAX_ACTIVE_RUNS_PER_USER: int = 1    # pending/running runs a user may have at once
# Runs executing at once across ALL users (Groq free tier + host memory). Set to 1 on small hosts.
MAX_CONCURRENT_RUNS: int = int(os.getenv("MAX_CONCURRENT_RUNS", "2"))
MAX_USER_ITERATIONS: int = 3         # highest max_iterations a user may request
RUN_TIMEOUT_SECONDS: int = 10 * 60   # a run still going after this is stopped (checked between steps)
# A run still pending/running this long after creation is treated as dead (stuck step, lost
# worker thread) and marked failed, so it can never block the user's active-run quota forever.
RUN_EXPIRY_SECONDS: int = RUN_TIMEOUT_SECONDS + 5 * 60

# --- Production checks ---
REQUIRED_IN_PRODUCTION = ("GROQ_API_KEY", "GOOGLE_CLIENT_ID", "DATABASE_URL", "FRONTEND_ORIGIN", "SEC_USER_AGENT")


def production_config_problems(env: dict[str, str] | None = None) -> list[str]:
    """Names of missing/invalid settings for production. Never includes secret values."""
    env = dict(os.environ) if env is None else env
    problems = [f"{name} is not set" for name in REQUIRED_IN_PRODUCTION if not env.get(name, "").strip()]
    provider = (env.get("EMBEDDING_PROVIDER", "").strip() or "local").lower()
    if provider not in ("local", "cohere"):
        problems.append(f"EMBEDDING_PROVIDER must be 'local' or 'cohere', not {provider!r}")
    if provider == "cohere" and not env.get("COHERE_API_KEY", "").strip():
        problems.append("COHERE_API_KEY is not set (required when EMBEDDING_PROVIDER=cohere)")
    database_url = env.get("DATABASE_URL", "")
    if database_url.startswith("sqlite"):
        problems.append("DATABASE_URL must point to Postgres in production (SQLite files are lost on redeploy)")
    origins = parse_origins(env.get("FRONTEND_ORIGIN", ""))
    # Origins are public URLs, so naming the bad one is safe (and makes the fix obvious)
    malformed = [origin for origin in origins if not ORIGIN_PATTERN.match(origin)]
    if malformed:
        problems.append(f"FRONTEND_ORIGIN has malformed origin(s) {malformed}; "
                        "expected e.g. https://app.vercel.app,http://localhost:5173 (no quotes)")
    if any(origin.startswith("http://") and "localhost" not in origin for origin in origins):
        problems.append("FRONTEND_ORIGIN must use https:// (except localhost)")
    return problems
