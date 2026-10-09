"""Auth tests: Google token verification rules and the get_current_user dependency."""

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.auth import AuthError, GoogleIdentity, get_current_user, verify_google_token
from src.db.models import User
from tests.api_helpers import auth_header, fake_verifier, memory_session_factory

CLAIMS = {"iss": "https://accounts.google.com", "sub": "1234567890", "email": "a@example.com",
          "email_verified": True, "aud": "client-id"}


def verifier_returning(claims: dict[str, Any]):  # type: ignore[no-untyped-def]
    calls: list[tuple] = []

    def verify(token: str, request: object, client_id: str) -> dict[str, Any]:
        calls.append((token, client_id))
        return claims

    verify.calls = calls  # type: ignore[attr-defined]
    return verify


# ---------- verify_google_token ----------

def test_valid_token_returns_identity_and_checks_our_client_id() -> None:
    verify = verifier_returning(CLAIMS)
    identity = verify_google_token("tok", client_id="client-id", verify=verify)
    assert identity == GoogleIdentity(sub="1234567890", email="a@example.com")
    assert verify.calls == [("tok", "client-id")]  # type: ignore[attr-defined]


def test_both_google_issuer_forms_accepted() -> None:
    identity = verify_google_token("tok", "client-id", verifier_returning(CLAIMS | {"iss": "accounts.google.com"}))
    assert identity.sub == "1234567890"


@pytest.mark.parametrize("bad_claims", [
    CLAIMS | {"iss": "https://evil.example.com"},
    CLAIMS | {"email_verified": False},
    {k: v for k, v in CLAIMS.items() if k != "email_verified"},
    {k: v for k, v in CLAIMS.items() if k != "sub"},
    {k: v for k, v in CLAIMS.items() if k != "email"},
], ids=["wrong-issuer", "unverified-email", "no-email-verified-claim", "no-sub", "no-email"])
def test_rejected_claims(bad_claims: dict[str, Any]) -> None:
    with pytest.raises(AuthError):
        verify_google_token("tok", "client-id", verifier_returning(bad_claims))


def test_invalid_signature_or_expiry_becomes_auth_error() -> None:
    def verify(token: str, request: object, client_id: str) -> dict[str, Any]:
        raise ValueError("Token expired")  # what google-auth raises for bad/expired tokens

    with pytest.raises(AuthError):
        verify_google_token("tok", "client-id", verify)


def test_missing_client_id_is_a_server_error_not_an_auth_error() -> None:
    with pytest.raises(RuntimeError):
        verify_google_token("tok", client_id=None, verify=verifier_returning(CLAIMS))


# ---------- get_current_user dependency ----------

@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.state.session_factory = memory_session_factory()
    app.state.token_verifier = fake_verifier

    @app.get("/whoami")
    def whoami(user: User = Depends(get_current_user)) -> dict[str, str]:
        return {"id": user.id, "email": user.email}

    return app


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": ""},
    {"Authorization": "token-alice"},          # no scheme
    {"Authorization": "Basic dXNlcjpwYXNz"},   # wrong scheme
    {"Authorization": "Bearer "},              # empty token
    {"Authorization": "Bearer not-a-valid-token"},
], ids=["missing", "empty", "no-scheme", "basic", "empty-token", "invalid-token"])
def test_unauthenticated_requests_get_401(app: FastAPI, headers: dict[str, str]) -> None:
    response = TestClient(app).get("/whoami", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_first_sign_in_creates_user(app: FastAPI) -> None:
    response = TestClient(app).get("/whoami", headers=auth_header("alice"))
    assert response.status_code == 200
    assert response.json() == {"id": "alice", "email": "alice@example.com"}
    with app.state.session_factory() as db:
        assert db.get(User, "alice") is not None


def test_bearer_scheme_is_case_insensitive(app: FastAPI) -> None:
    response = TestClient(app).get("/whoami", headers={"Authorization": "bearer token-alice"})
    assert response.status_code == 200


def test_changed_google_email_is_updated(app: FastAPI) -> None:
    client = TestClient(app)
    client.get("/whoami", headers=auth_header("alice"))

    def new_email(token: str) -> GoogleIdentity:
        return GoogleIdentity(sub="alice", email="alice.new@example.com")

    app.state.token_verifier = new_email
    response = client.get("/whoami", headers=auth_header("alice"))
    assert response.json() == {"id": "alice", "email": "alice.new@example.com"}


# ---------- Live (opt-in): the REAL Google verifier rejects a forged token ----------

@pytest.mark.live
def test_live_google_rejects_garbage_token() -> None:
    from src.config import GOOGLE_CLIENT_ID

    forged = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZha2UifQ.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    with pytest.raises(AuthError):
        verify_google_token(forged, client_id=GOOGLE_CLIENT_ID)
