"""GroqClient tests with a fake SDK and real groq exception objects (no network)."""

from types import SimpleNamespace
from typing import Any

import groq
import httpx
import pytest

from src.clients.groq_client import (
    GroqClient,
    LLMConfigError,
    LLMUnavailableError,
    MalformedToolCallError,
)

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
OK_MESSAGE = SimpleNamespace(content="hello", tool_calls=None)


def ok_response() -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=OK_MESSAGE)])


def status_error(cls: type[groq.APIStatusError], status: int, body: Any = None,
                 headers: dict[str, str] | None = None) -> groq.APIStatusError:
    response = httpx.Response(status, request=REQUEST, headers=headers or {})
    return cls(f"HTTP {status}", response=response, body=body)


class FakeSDK:
    """Mimics groq.Groq: `.chat.completions.create(**kwargs)` replays a script of outcomes."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_client(outcomes: list[Any]) -> tuple[GroqClient, FakeSDK, list[float]]:
    sdk = FakeSDK(outcomes)
    sleeps: list[float] = []
    client = GroqClient(sdk=sdk, sleep=sleeps.append, max_attempts=3, base_delay=1.0)
    return client, sdk, sleeps


def test_happy_path_returns_message_and_passes_arguments() -> None:
    client, sdk, sleeps = make_client([ok_response()])
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    message = client.chat([{"role": "user", "content": "hi"}], model="m", tools=tools, tool_choice="required")
    assert message is OK_MESSAGE
    assert sdk.requests[0]["model"] == "m"
    assert sdk.requests[0]["tools"] == tools
    assert sdk.requests[0]["tool_choice"] == "required"
    assert sdk.requests[0]["temperature"] == 0.0
    assert sleeps == []


def test_omits_tools_when_not_given() -> None:
    client, sdk, _ = make_client([ok_response()])
    client.chat([{"role": "user", "content": "hi"}], model="m")
    assert "tools" not in sdk.requests[0] and "tool_choice" not in sdk.requests[0]


def test_rate_limit_retried_with_exponential_backoff() -> None:
    rate_limited = status_error(groq.RateLimitError, 429)
    client, sdk, sleeps = make_client([rate_limited, rate_limited, ok_response()])
    assert client.chat([], model="m") is OK_MESSAGE
    assert len(sdk.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_retry_after_header_respected_and_capped() -> None:
    short = status_error(groq.RateLimitError, 429, headers={"retry-after": "5"})
    huge = status_error(groq.RateLimitError, 429, headers={"retry-after": "600"})
    client, _, sleeps = make_client([short, huge, ok_response()])
    client.chat([], model="m")
    assert sleeps == [5.0, 60.0]


def test_server_error_and_timeout_are_retried() -> None:
    server_error = status_error(groq.InternalServerError, 503)
    timeout = groq.APITimeoutError(request=REQUEST)
    client, sdk, sleeps = make_client([server_error, timeout, ok_response()])
    assert client.chat([], model="m") is OK_MESSAGE
    assert len(sdk.requests) == 3


def test_gives_up_after_max_attempts() -> None:
    connection_error = groq.APIConnectionError(request=REQUEST)
    client, sdk, sleeps = make_client([connection_error] * 3)
    with pytest.raises(LLMUnavailableError):
        client.chat([], model="m")
    assert len(sdk.requests) == 3
    assert sleeps == [1.0, 2.0]  # no sleep after the final attempt


@pytest.mark.parametrize(
    ("cls", "status"),
    [(groq.BadRequestError, 400), (groq.AuthenticationError, 401), (groq.NotFoundError, 404)],
)
def test_non_retryable_errors_raised_immediately(cls: type[groq.APIStatusError], status: int) -> None:
    client, sdk, sleeps = make_client([status_error(cls, status, body={"error": {"code": "other"}})])
    with pytest.raises(cls):
        client.chat([], model="m")
    assert len(sdk.requests) == 1
    assert sleeps == []


def test_tool_use_failed_becomes_malformed_tool_call_error() -> None:
    body = {"error": {"code": "tool_use_failed", "message": "Tool choice is required",
                      "failed_generation": "I can't find a US ticker"}}
    client, sdk, _ = make_client([status_error(groq.BadRequestError, 400, body=body)])
    with pytest.raises(MalformedToolCallError) as excinfo:
        client.chat([], model="m")
    assert excinfo.value.failed_generation == "I can't find a US ticker"
    assert len(sdk.requests) == 1


def test_missing_api_key_raises_config_error() -> None:
    with pytest.raises(LLMConfigError):
        GroqClient(api_key=None)
    with pytest.raises(LLMConfigError):
        GroqClient(api_key="")


@pytest.mark.live
def test_live_simple_chat() -> None:
    from src.config import TOOL_CALL_MODEL

    message = GroqClient().chat([{"role": "user", "content": "Reply with the word OK."}], model=TOOL_CALL_MODEL)
    assert message.content and "OK" in message.content.upper()


def test_default_client_tries_four_times() -> None:
    connection_error = groq.APIConnectionError(request=REQUEST)
    sdk = FakeSDK([connection_error] * 4)
    client = GroqClient(sdk=sdk, sleep=lambda s: None)
    with pytest.raises(LLMUnavailableError):
        client.chat([], model="m")
    assert len(sdk.requests) == 4


def test_sdk_request_timeout_is_set() -> None:
    client = GroqClient(api_key="gsk_test_placeholder")
    assert client._sdk.timeout == 60.0  # not the SDK default of 10 minutes
