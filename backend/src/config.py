"""Central configuration: loads environment variables once and exposes constants.

Secrets (API keys, client IDs) are read from the environment / backend/.env and
are never hardcoded here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ directory (this file lives in backend/src/config.py)
BACKEND_DIR = Path(__file__).resolve().parent.parent

# Load backend/.env if it exists. Real environment variables take precedence.
load_dotenv(BACKEND_DIR / ".env")

# --- Secrets / deployment settings ---
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GOOGLE_CLIENT_ID: str | None = os.getenv("GOOGLE_CLIENT_ID")
FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

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
DATABASE_URL: str = normalize_database_url(os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'app.db'}"))

# --- Model names (Groq) ---
# Groq retired its Llama chat models (see DECISIONS D11/D23); both are open-weight gpt-oss models.
TOOL_CALL_MODEL: str = "openai/gpt-oss-20b"    # smaller + faster, used for tool calling / extraction
REASONING_MODEL: str = "openai/gpt-oss-120b"   # larger model for analyst/critic/auditor reasoning

# --- SEC filings / RAG (Stage 4) ---
# SEC requires a User-Agent with a name and contact email on every EDGAR request.
SEC_USER_AGENT: str | None = os.getenv("SEC_USER_AGENT")
FILINGS_DIR = BACKEND_DIR / "data" / "filings"  # downloaded filings + manually added PDFs (gitignored)
CHROMA_DIR = BACKEND_DIR / "chroma_db"           # persistent vector store (gitignored)
TOP_K_CHUNKS: int = 8                            # filing excerpts given to the agents per run

# --- Graph guardrail ---
# Hard cap on Analyst <-> Critic rounds before the graph is forced to exit.
MAX_ITERATIONS: int = 3

# --- API quotas (Stage 6), checked BEFORE any graph work ---
RUNS_PER_DAY: int = 5                # research runs per user per rolling 24 hours
MAX_ACTIVE_RUNS_PER_USER: int = 1    # pending/running runs a user may have at once
MAX_CONCURRENT_RUNS: int = 2         # runs executing at once across ALL users (Groq free tier)
MAX_USER_ITERATIONS: int = 3         # highest max_iterations a user may request
