"""Shared helpers for API tests: an in-memory DB and a fake Google token verifier."""

from sqlalchemy.orm import sessionmaker

from src.api.auth import AuthError, GoogleIdentity
from src.db.session import init_db, make_engine, make_session_factory

# Fake tokens: "token-<sub>" signs in as user <sub> with email <sub>@example.com
TOKEN_PREFIX = "token-"


def fake_verifier(token: str) -> GoogleIdentity:
    if not token.startswith(TOKEN_PREFIX):
        raise AuthError("Invalid or expired Google ID token")
    sub = token[len(TOKEN_PREFIX):]
    return GoogleIdentity(sub=sub, email=f"{sub}@example.com")


def auth_header(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN_PREFIX}{sub}"}


def memory_session_factory() -> sessionmaker:
    """A fresh shared in-memory SQLite database with all tables."""
    engine = make_engine("sqlite://")
    init_db(engine)
    return make_session_factory(engine)
