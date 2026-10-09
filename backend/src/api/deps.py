"""Shared FastAPI dependencies.

The app keeps its collaborators on `app.state` (session factory, token verifier, run
worker). create_app() in main.py sets real ones; tests build an app with fakes.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db(request: Request) -> Iterator[Session]:
    """One DB session per request, always closed."""
    db: Session = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()
