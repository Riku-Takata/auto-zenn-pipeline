"""LLM client interfaces and the Claude Code CLI implementation."""

import json
import re
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from auto_zenn_pipeline.errors import LlmOutputError
from auto_zenn_pipeline.runner import CommandRunner, SubprocessRunner

T = TypeVar("T", bound=BaseModel)

_SCHEMA_INSTRUCTION = (
    "\n\n次の JSON Schema に合う JSON オブジェクトだけを返してください。"
    "説明の文章やコードブロックは付けないでください。\n\n"
)
_FENCE = re.compile(r"```(?:json)?[ \t]*\n(.*?)```", re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    candidates: list[str] = [text.strip()]

    match = _FENCE.search(text)
    if match is not None:
        candidates.append(match.group(1).strip())

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


class ToolLlmClient(Protocol):
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


class LlmClient(Protocol):
    def generate(
        self,
        *,
        session: str,
        step: str,
        model: str,
        system: str,
        user: str,
        output_type: type[T],
    ) -> T: ...


class ClaudeCliClient:
    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._runner: CommandRunner = (
            runner if runner is not None else SubprocessRunner()
        )
        self._cwd: Path = cwd if cwd is not None else Path.cwd()

    def generate(
        self,
        *,
        session: str,
        step: str,
        model: str,
        system: str,
        user: str,
        output_type: type[T],
    ) -> T:
        schema = json.dumps(output_type.model_json_schema(), ensure_ascii=False)
        system_prompt = system + _SCHEMA_INSTRUCTION + schema
        args = [
            "claude",
            "-p",
            "--model",
            model,
            "--output-format",
            "json",
            "--tools",
            "",
            "--system-prompt",
            system_prompt,
        ]
        result = self._runner.run(args, cwd=self._cwd, input=user)
        if result.returncode != 0:
            raise LlmOutputError(session, step)

        try:
            envelope = json.loads(result.stdout)
        except ValueError as exc:
            raise LlmOutputError(session, step) from exc

        if not isinstance(envelope, dict):
            raise LlmOutputError(session, step)
        if envelope.get("is_error") is True:
            raise LlmOutputError(session, step)
        res_text = envelope.get("result")
        if not isinstance(res_text, str):
            raise LlmOutputError(session, step)

        data = _extract_json_object(res_text)
        if data is None:
            raise LlmOutputError(session, step)

        try:
            return output_type.model_validate(data)
        except ValidationError as exc:
            raise LlmOutputError(session, step) from exc
