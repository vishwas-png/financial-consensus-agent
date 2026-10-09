"""Generic "LLM must answer by filling in this Pydantic schema" helper.

How it works:
1. Turn the Pydantic model into a tool definition (JSON Schema).
2. Force the model to call that tool (`tool_choice`), so it can't answer in free text.
3. Validate the tool arguments with Pydantic.
4. If anything is wrong, give the model ONE chance to fix it, showing it the error.
5. If it is still wrong, raise ToolCallError. Bad data never reaches the caller.

Used for ticker extraction (Stage 2) and for every agent's structured output (Stage 3+).
No finance logic lives here.
"""

import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from src.clients.groq_client import GroqClient, MalformedToolCallError

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

# 1 normal attempt + 1 repair attempt
MAX_ATTEMPTS = 2


class ToolCallError(Exception):
    """The model failed to produce a valid tool call, even after a repair attempt."""

    def __init__(self, reason: str, raw_output: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw_output = raw_output


def pydantic_to_tool(schema: type[BaseModel], name: str, description: str) -> dict[str, Any]:
    """Build a Groq/OpenAI function-tool definition from a Pydantic model."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema.model_json_schema(),
        },
    }


def _parse_tool_call(message: Any, schema: type[SchemaT], tool_name: str) -> SchemaT:
    """Validate one assistant message. Raises ToolCallError describing what is wrong."""
    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        raise ToolCallError(
            f"You answered in plain text instead of calling the `{tool_name}` tool.",
            raw_output=getattr(message, "content", None),
        )

    call = tool_calls[0]
    raw_arguments = call.function.arguments
    if call.function.name != tool_name:
        raise ToolCallError(
            f"You called `{call.function.name}`, but you must call `{tool_name}`.",
            raw_output=raw_arguments,
        )

    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError) as error:
        raise ToolCallError(f"Tool arguments were not valid JSON: {error}", raw_output=raw_arguments) from error

    try:
        return schema.model_validate(arguments)
    except ValidationError as error:
        # Short, model-readable list of problems, e.g. "ticker: must not include an exchange suffix"
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in error.errors()
        )
        raise ToolCallError(f"Tool arguments failed validation: {problems}", raw_output=raw_arguments) from error


def call_with_schema(
    client: GroqClient,
    messages: list[dict[str, Any]],
    schema: type[SchemaT],
    tool_name: str,
    description: str,
    model: str,
    extra_check: Callable[[SchemaT], None] | None = None,
) -> SchemaT:
    """Force the model to call `tool_name` and return its arguments as a validated `schema` instance.

    `extra_check` is for rules a JSON schema can't express (e.g. "citations must be ids we
    actually provided"). It raises ValueError with a model-readable message; that triggers
    the same single repair attempt as a schema error.

    Raises:
        ToolCallError: still invalid after one repair attempt.
        LLMUnavailableError / groq errors: propagated from the client (not a model mistake).
    """
    tool = pydantic_to_tool(schema, tool_name, description)
    tool_choice = {"type": "function", "function": {"name": tool_name}}
    conversation = list(messages)  # don't mutate the caller's list
    last_error: ToolCallError | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            message = client.chat(conversation, model=model, tools=[tool], tool_choice=tool_choice)
            result = _parse_tool_call(message, schema, tool_name)
            if extra_check is not None:
                try:
                    extra_check(result)
                except ValueError as error:
                    raise ToolCallError(
                        f"Output failed a content check: {error}", raw_output=result.model_dump_json()
                    ) from error
            if attempt > 1:
                logger.info("Tool call `%s` succeeded after repair (attempt %d)", tool_name, attempt)
            return result
        except MalformedToolCallError as error:
            # Groq itself rejected the output (e.g. the model refused to call the tool)
            last_error = ToolCallError(
                f"Your response was not a valid `{tool_name}` call: {error}",
                raw_output=error.failed_generation,
            )
        except ToolCallError as error:
            last_error = error

        logger.warning("Tool call `%s` attempt %d/%d invalid: %s", tool_name, attempt, MAX_ATTEMPTS, last_error.reason)
        # Repair: show the model its own output and what was wrong, then ask again
        conversation = conversation + [
            {
                "role": "user",
                "content": (
                    f"Your previous response was invalid. Problem: {last_error.reason}\n"
                    f"Your output was: {last_error.raw_output!r}\n"
                    f"Call the `{tool_name}` tool again with corrected arguments."
                ),
            }
        ]

    assert last_error is not None
    raise ToolCallError(
        f"`{tool_name}` failed after {MAX_ATTEMPTS} attempts. Last problem: {last_error.reason}",
        raw_output=last_error.raw_output,
    )
