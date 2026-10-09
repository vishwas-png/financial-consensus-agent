"""Google sign-in: verify the ID token on every request and map it to a user.

The frontend (Google Identity Services) gets a signed Google ID token (a JWT) and
sends it as `Authorization: Bearer <token>`. We verify it ourselves on every request:
signature (Google's public keys), expiry, audience (our GOOGLE_CLIENT_ID), issuer and
email_verified. The token's `sub` claim is the user's id. No server sessions, no passwords.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy.orm import Session

from src.api.deps import get_db
from src.config import GOOGLE_CLIENT_ID
from src.db.models import User

VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class AuthError(Exception):
    """The token is missing, malformed, invalid, expired or not ours."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str     # Google's stable account id -> users.id
    email: str


TokenVerifier = Callable[[str], GoogleIdentity]


def verify_google_token(
    token: str,
    client_id: str | None = GOOGLE_CLIENT_ID,
    verify: Callable[..., dict[str, Any]] = id_token.verify_oauth2_token,
) -> GoogleIdentity:
    """Verify a Google ID token and return who it belongs to. `verify` is injectable for tests."""
    if not client_id:
        raise RuntimeError("GOOGLE_CLIENT_ID is not set. Add it to backend/.env.")
    try:
        # Checks signature against Google's public keys, expiry, and audience == client_id
        claims = verify(token, google_requests.Request(), client_id)
    except ValueError as error:
        raise AuthError("Invalid or expired Google ID token") from error

    if claims.get("iss") not in VALID_ISSUERS:
        raise AuthError("Token was not issued by Google")
    if not claims.get("sub") or not claims.get("email"):
        raise AuthError("Token is missing the account id or email")
    if not claims.get("email_verified"):
        raise AuthError("Google account email is not verified")
    return GoogleIdentity(sub=str(claims["sub"]), email=str(claims["email"]))


def _bearer_token(authorization: str | None) -> str:
    """Extract the token from 'Bearer <token>' (scheme is case-insensitive)."""
    if not authorization:
        raise AuthError("Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Authorization header must be 'Bearer <token>'")
    return token.strip()


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: the signed-in user, created on first sign-in. 401 on any auth problem."""
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        identity = verifier(_bearer_token(authorization))
    except AuthError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    user = db.get(User, identity.sub)
    if user is None:
        user = User(id=identity.sub, email=identity.email)
        db.add(user)
        db.commit()
    elif user.email != identity.email:
        user.email = identity.email  # the Google account's email changed; sub stays the same
        db.commit()
    return user
