"""LLM client interface and Anthropic implementation."""

from typing import Any, Protocol, cast

import anthropic  # type: ignore[import-not-found,unused-ignore]

from auto_zenn_pipeline.errors import LlmOutputError


class LlmClient(Protocol):
    def call_tool(
        self,
        *,
        session: str,
        step: str,
        model: str,
        system: str,
        user: str,
        tool_name: str,
        input_schema: dict[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]: ...


class AnthropicLlmClient:
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client if client is not None else anthropic.Anthropic()

    def call_tool(
        self,
        *,
        session: str,
        step: str,
        model: str,
        system: str,
        user: str,
        tool_name: str,
        input_schema: dict[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]:
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": tool_name,
                    "description": f"Return the result by calling {tool_name}.",
                    "input_schema": input_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
        if response.stop_reason == "max_tokens":
            raise LlmOutputError(session, step)

        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if getattr(block, "name", None) != tool_name:
                continue
            tool_input = getattr(block, "input", None)
            if not isinstance(tool_input, dict):
                raise LlmOutputError(session, step)
            return cast(dict[str, Any], tool_input)

        raise LlmOutputError(session, step)
