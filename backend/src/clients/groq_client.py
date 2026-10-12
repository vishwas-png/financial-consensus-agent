"""Thin wrapper around the Groq SDK with explicit retry and exponential backoff.

The SDK's own retries are turned off (max_retries=0) so there is exactly one
retry loop, and it lives here where it can be read and tested.

Retried (temporary problems): rate limits (429), connection errors, timeouts, 5xx.
Not retried (our fault, retrying won't help): bad request (400), auth (401/403), not found (404).
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import groq

from src.config import GROQ_API_KEY
from src.observability import current_metrics

logger = logging.getLogger(__name__)

RETRYABLE_ERRORS = (
    groq.RateLimitError,
    groq.APIConnectionError,  # also covers APITimeoutError (a subclass)
    groq.InternalServerError,
)
MAX_RETRY_AFTER_SECONDS = 60.0  # Groq token-per-minute resets can take ~50s
REQUEST_TIMEOUT_SECONDS = 60.0  # one HTTP request to Groq; a timeout counts as a retryable error


class LLMError(Exception):
    """Base class for all LLM client errors."""


class LLMConfigError(LLMError):
    """Client is misconfigured (e.g. missing API key)."""


class LLMUnavailableError(LLMError):
    """Groq kept failing with temporary errors after all retry attempts."""


class MalformedToolCallError(LLMError):
    """Groq rejected the model's output as an invalid tool call (code `tool_use_failed`).

    `failed_generation` holds what the model actually produced, so the caller
    can show it back to the model in a repair attempt.
    """

    def __init__(self, message: str, failed_generation: str | None = None) -> None:
        super().__init__(message)
        self.failed_generation = failed_generation


def _retry_after_seconds(error: Exception) -> float | None:
    """Read the `retry-after` header from a 429 response, if the server sent one."""
    response = getattr(error, "response", None)
    if response is None:
        return None
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _tool_use_failed(error: groq.BadRequestError) -> MalformedToolCallError | None:
    """Convert Groq's 400 `tool_use_failed` into MalformedToolCallError; None for other 400s."""
    body = error.body if isinstance(error.body, dict) else {}
    details = body.get("error", body)  # body is usually {"error": {...}}
    if not isinstance(details, dict) or details.get("code") != "tool_use_failed":
        return None
    return MalformedToolCallError(
        details.get("message", "Model produced an invalid tool call"),
        failed_generation=details.get("failed_generation"),
    )


class GroqClient:
    """Groq chat client with retry/backoff. Inject `sdk` and `sleep` in tests."""

    def __init__(
        self,
        api_key: str | None = GROQ_API_KEY,
        max_attempts: int = 4,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        sdk: Any = None,
        request_timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if sdk is None:
            api_key = (api_key or "").strip()  # a stray newline would break the auth header
            if not api_key:
                raise LLMConfigError("GROQ_API_KEY is not set. Add it to backend/.env.")
            # The SDK's default timeout is 10 minutes; a hung request would block a worker thread
            sdk = groq.Groq(api_key=api_key, max_retries=0, timeout=request_timeout)
        self._sdk = sdk
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self._sleep = sleep

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float = 0.0,
    ) -> Any:
        """Send one chat completion request and return the assistant message.

        Raises:
            MalformedToolCallError: the model's tool call could not be parsed by Groq.
            LLMUnavailableError: temporary errors on every attempt.
            LLMConfigError: Groq rejected the API key (401/403); retrying won't help.
            LLMError: any other non-retryable HTTP error (bad request, not found, ...).
        """
        request: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if tools is not None:
            request["tools"] = tools
        if tool_choice is not None:
            request["tool_choice"] = tool_choice

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._sdk.chat.completions.create(**request)
                _record_usage(model, response)
                return response.choices[0].message
            except groq.BadRequestError as error:
                malformed = _tool_use_failed(error)
                if malformed is not None:
                    raise malformed from error
                raise LLMError(f"Groq rejected the request (HTTP 400): {error}") from error
            except (groq.AuthenticationError, groq.PermissionDeniedError) as error:
                # Our own error type, so the API answers with a handled 502 instead of a crash
                raise LLMConfigError(
                    f"Groq rejected the API key (HTTP {error.status_code}). Check GROQ_API_KEY."
                ) from error
            except RETRYABLE_ERRORS as error:
                if attempt == self.max_attempts:
                    raise LLMUnavailableError(
                        f"Groq failed after {self.max_attempts} attempts: {error}"
                    ) from error
                # Exponential backoff: 1s, 2s, 4s... unless the server says how long to wait
                delay = self.base_delay * (2 ** (attempt - 1))
                retry_after = _retry_after_seconds(error)
                if retry_after is not None:
                    delay = min(retry_after, MAX_RETRY_AFTER_SECONDS)
                # Log only the cause's TYPE: its message can contain request headers (the API key)
                cause = type(error.__cause__).__name__ if error.__cause__ else "none"
                logger.warning(
                    "Groq %s (cause: %s) on attempt %d/%d; retrying in %.1fs",
                    type(error).__name__, cause, attempt, self.max_attempts, delay,
                )
                metrics = current_metrics()
                if metrics is not None:
                    metrics.add_llm_retry()
                self._sleep(delay)
            except groq.APIStatusError as error:  # e.g. 404 unknown model (after the retryable ones)
                raise LLMError(f"Groq HTTP {error.status_code}: {error}") from error

        raise AssertionError("unreachable")  # the loop always returns or raises


def _record_usage(model: str, response: Any) -> None:
    """Report token usage to the current run's metrics (no-op outside a run)."""
    metrics = current_metrics()
    if metrics is None:
        return
    usage = getattr(response, "usage", None)
    metrics.add_llm_call(
        model,
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        retries=0,  # retries are counted as they happen (add_llm_retry)
    )
