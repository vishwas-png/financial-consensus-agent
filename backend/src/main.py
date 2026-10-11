"""FastAPI app: settings check, logging, CORS, security headers, routers, startup tasks.

Run locally (from backend/):  uvicorn src.main:app --reload

create_app() takes its collaborators as parameters (DB sessions, token verifier,
run worker, ticker extractor), so tests build an app with fakes instead of patching.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import sessionmaker

from src.api.auth import TokenVerifier, verify_google_token
from src.api.jobs import RunWorker
from src.api.middleware import SecurityHeadersMiddleware, UnhandledErrorMiddleware
from src.api.routes_research import router as research_router
from src.config import FRONTEND_ORIGINS, IS_PRODUCTION, production_config_problems
from src.db.models import Base, mark_stale_runs_failed
from src.db.session import SessionLocal

STREAM_POLL_SECONDS = 0.5
STREAM_MAX_SECONDS = 30 * 60  # a run that hasn't finished after 30 minutes is treated as stuck

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """One readable log format; quiet noisy libraries."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # yfinance logs "HTTP Error 404 ... possibly delisted" at ERROR for unknown tickers,
    # which we already handle (TickerNotFoundError); keep only its critical messages.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def extract_ticker_with_llm(query: str) -> str:
    """Default ticker extractor: the Stage 2 tool-calling step (small model, ~2-5 s)."""
    from src.clients.groq_client import GroqClient
    from src.core.tool_calling import extract_ticker

    return extract_ticker(query, GroqClient()).ticker


def create_app(
    session_factory: sessionmaker = SessionLocal,
    token_verifier: TokenVerifier = verify_google_token,
    worker: RunWorker | None = None,
    ticker_extractor: Callable[[str], str] = extract_ticker_with_llm,
    stream_poll_seconds: float = STREAM_POLL_SECONDS,
    stream_max_seconds: float = STREAM_MAX_SECONDS,
    frontend_origins: list[str] | None = None,
    production: bool = IS_PRODUCTION,
) -> FastAPI:
    if production:
        problems = production_config_problems()
        if problems:
            # Fail at startup with a clear list, instead of failing on the first user request
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))

    worker = worker or RunWorker(session_factory)
    allowed_origins = frontend_origins if frontend_origins is not None else FRONTEND_ORIGINS

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = session_factory.kw["bind"]
        if engine.dialect.name == "sqlite":
            # Local dev and tests: create tables if missing. Postgres (deployed) is managed
            # only by Alembic migrations (`alembic upgrade head` runs before the server starts).
            Base.metadata.create_all(bind=engine)
        with session_factory() as db:
            mark_stale_runs_failed(db)  # runs interrupted by the last shutdown can't resume
        logger.info("CORS allowed origins: %s", allowed_origins)  # public URLs, not secrets
        yield
        worker.shutdown()

    app = FastAPI(title="Financial Consensus Agent API", version="0.8.0", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.token_verifier = token_verifier
    app.state.worker = worker
    app.state.ticker_extractor = ticker_extractor
    app.state.stream_poll_seconds = stream_poll_seconds
    app.state.stream_max_seconds = stream_max_seconds

    # Middleware added FIRST runs INNERMOST. This one must sit inside CORS so even a crash
    # returns a 500 with CORS headers (otherwise browsers report a misleading CORS error).
    app.add_middleware(UnhandledErrorMiddleware)
    # Only our frontend(s) may call the API from a browser. No cookies are used (Bearer tokens).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(research_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


configure_logging()
app = create_app()
