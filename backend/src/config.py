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

# Default to a local SQLite file inside backend/. In deployment this points to Postgres.
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'app.db'}")

# --- Model names (Groq) ---
TOOL_CALL_MODEL: str = "llama-3.1-8b-instant"   # cheap + fast, used for tool calling
REASONING_MODEL: str = "llama-3.3-70b-versatile"  # larger model for analyst/critic/auditor reasoning

# --- Graph guardrail ---
# Hard cap on Analyst <-> Critic rounds before the graph is forced to exit.
MAX_ITERATIONS: int = 3
