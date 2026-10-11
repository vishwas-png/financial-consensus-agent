"""Production config checks, origin parsing, CORS for several origins, security headers."""

import pytest
from fastapi.testclient import TestClient

from src.config import env_str, parse_origins, production_config_problems
from src.main import create_app
from tests.api_helpers import auth_header, fake_verifier, memory_session_factory

GOOD_ENV = {
    "GROQ_API_KEY": "gsk_placeholder",
    "GOOGLE_CLIENT_ID": "x.apps.googleusercontent.com",
    "DATABASE_URL": "postgresql://u:p@db.example.com/app",
    "FRONTEND_ORIGIN": "https://fca.vercel.app,http://localhost:5173",
    "SEC_USER_AGENT": "Jane Doe jane@example.com",
}


def test_parse_origins() -> None:
    assert parse_origins(" https://a.app/ , http://localhost:5173,, ") == ["https://a.app", "http://localhost:5173"]
    assert parse_origins("") == []


def test_complete_production_config_has_no_problems() -> None:
    assert production_config_problems(GOOD_ENV) == []


@pytest.mark.parametrize(("change", "expected"), [
    ({"GROQ_API_KEY": ""}, "GROQ_API_KEY is not set"),
    ({"GOOGLE_CLIENT_ID": "   "}, "GOOGLE_CLIENT_ID is not set"),
    ({"SEC_USER_AGENT": ""}, "SEC_USER_AGENT is not set"),
    ({"DATABASE_URL": "sqlite:///./app.db"}, "must point to Postgres"),
    ({"FRONTEND_ORIGIN": "http://fca.example.com"}, "must use https://"),
    ({"FRONTEND_ORIGIN": '"https://fca.vercel.app,http://localhost:5173"'}, "malformed origin"),
    ({"FRONTEND_ORIGIN": "fca.vercel.app"}, "malformed origin"),
    ({"FRONTEND_ORIGIN": "https://fca.vercel.app/app"}, "malformed origin"),
    ({"FRONTEND_ORIGIN": "*"}, "malformed origin"),
])
def test_production_problems_detected(change: dict[str, str], expected: str) -> None:
    problems = production_config_problems(GOOD_ENV | change)
    assert any(expected in problem for problem in problems), problems


@pytest.mark.parametrize("value", ["https://fca.vercel.app,http://localhost:5173",
                                   " https://fca.vercel.app/ ", "http://localhost:5173"])
def test_well_formed_origins_accepted(value: str) -> None:
    assert production_config_problems(GOOD_ENV | {"FRONTEND_ORIGIN": value}) == []


def test_problems_never_contain_secret_values() -> None:
    env = GOOD_ENV | {"DATABASE_URL": "sqlite:///secret-path.db", "GOOGLE_CLIENT_ID": ""}
    text = " ".join(production_config_problems(env))
    assert "gsk_placeholder" not in text and "secret-path" not in text


def test_production_app_refuses_to_start_when_misconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in GOOD_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="Refusing to start in production"):
        create_app(session_factory=memory_session_factory(), production=True)


def test_production_app_starts_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in GOOD_ENV.items():
        monkeypatch.setenv(name, value)
    create_app(session_factory=memory_session_factory(), production=True)


# ---------- CORS with several origins + security headers ----------

@pytest.fixture
def client() -> TestClient:
    app = create_app(session_factory=memory_session_factory(), token_verifier=fake_verifier,
                     frontend_origins=["https://fca.vercel.app", "http://localhost:5173"])
    return TestClient(app)


PREFLIGHT = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}


@pytest.mark.parametrize("origin", ["https://fca.vercel.app", "http://localhost:5173"])
def test_cors_allows_each_configured_origin(client: TestClient, origin: str) -> None:
    response = client.options("/research", headers={"Origin": origin, **PREFLIGHT})
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_blocks_other_origins(client: TestClient) -> None:
    response = client.options("/research", headers={"Origin": "https://fca.vercel.app.evil.com", **PREFLIGHT})
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("path", ["/health", "/history", "/does-not-exist"])
def test_security_headers_on_every_response(client: TestClient, path: str) -> None:
    response = client.get(path, headers=auth_header("alice"))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_env_str_strips_pasted_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCA_TEST_VALUE", "  gsk_placeholder\r\n")
    assert env_str("FCA_TEST_VALUE") == "gsk_placeholder"
    monkeypatch.setenv("FCA_TEST_VALUE", " \n")
    assert env_str("FCA_TEST_VALUE") is None
    assert env_str("FCA_TEST_VALUE", "fallback") == "fallback"
