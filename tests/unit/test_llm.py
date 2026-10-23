"""Tests for LlmClient and ClaudeCliClient."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

import auto_zenn_pipeline.llm as llm_module
from auto_zenn_pipeline.errors import LlmOutputError
from auto_zenn_pipeline.llm import ClaudeCliClient, LlmClient
from auto_zenn_pipeline.runner import CommandResult, SubprocessRunner


class M(BaseModel):
    title: str
    n: int


class _FakeRunner:
    def __init__(self, result: CommandResult | None = None) -> None:
        self.result = (
            result
            if result is not None
            else CommandResult(
                returncode=0,
                stdout=json.dumps(
                    {
                        "type": "result",
                        "is_error": False,
                        "result": json.dumps({"title": "hello", "n": 42}),
                    }
                ),
                stderr="",
            )
        )
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        input: str | None = None,
    ) -> CommandResult:
        self.calls.append({"args": list(args), "cwd": cwd, "input": input})
        return self.result


def test_protocol_assignable(tmp_path: Path) -> None:
    # AC-1, AC-2
    fake = _FakeRunner()
    client: LlmClient = ClaudeCliClient(runner=fake, cwd=tmp_path)
    assert client is not None


def test_default_constructor() -> None:
    # AC-2
    client = ClaudeCliClient()
    assert isinstance(client._runner, SubprocessRunner)
    assert client._cwd == Path.cwd()


def test_generate_calls_fake_runner_once(tmp_path: Path) -> None:
    # AC-4, AC-5
    fake = _FakeRunner()
    client = ClaudeCliClient(runner=fake, cwd=tmp_path)
    res = client.generate(
        session="s",
        step="article",
        model="claude-sonnet-5-5",
        system="SYS",
        user="USER",
        output_type=M,
    )
    assert isinstance(res, M)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    expected_schema = json.dumps(M.model_json_schema(), ensure_ascii=False)
    args = call["args"]
    assert len(args) == 10
    assert args[:9] == [
        "claude",
        "-p",
        "--model",
        "claude-sonnet-5-5",
        "--output-format",
        "json",
        "--tools",
        "",
        "--system-prompt",
    ]
    assert call["cwd"] == tmp_path
    assert call["input"] == "USER"
    assert args[9].startswith("SYS")
    assert expected_schema in args[9]


@pytest.mark.parametrize(
    "body",
    [
        # (a) 本文全体が JSON オブジェクト(前後の空白は許す)
        '  {"title": "t1", "n": 10}  \n',
        # (b) 前後に文章があり、JSON オブジェクトが ```json か ``` のコードブロックに入っている
        'Here is the response:\n```json\n{"title": "t1", "n": 10}\n```\nHope it helps.',
        'Here is the response:\n```\n{"title": "t1", "n": 10}\n```\nHope it helps.',
        # (c) 前後に文章があり、本文の最初の { から最後の } までが JSON オブジェクト
        'Leading text {"title": "t1", "n": 10} trailing text',
    ],
)
def test_extract_json_valid_formats(tmp_path: Path, body: str) -> None:
    # AC-6
    envelope = {
        "type": "result",
        "is_error": False,
        "result": body,
    }
    fake = _FakeRunner(
        CommandResult(returncode=0, stdout=json.dumps(envelope), stderr="")
    )
    client = ClaudeCliClient(runner=fake, cwd=tmp_path)
    res = client.generate(
        session="s",
        step="article",
        model="claude-sonnet-5-5",
        system="SYS",
        user="USER",
        output_type=M,
    )
    assert res == M(title="t1", n=10)
    assert res.model_dump() == {"title": "t1", "n": 10}


@pytest.mark.parametrize(
    "result",
    [
        # returncode が 0 でない
        CommandResult(
            returncode=1,
            stdout='{"type":"result","is_error":false,"result":"{\\"title\\":\\"t\\",\\"n\\":1}"}',
            stderr="err",
        ),
        # stdout が JSON として読めない
        CommandResult(returncode=0, stdout="not a json", stderr=""),
        # stdout が dict でない
        CommandResult(returncode=0, stdout="[1, 2, 3]", stderr=""),
        # stdout の JSON に文字列の result が無い (キーが無い)
        CommandResult(
            returncode=0, stdout='{"type":"result","is_error":false}', stderr=""
        ),
        # stdout の JSON に文字列の result が無い (result が数値)
        CommandResult(
            returncode=0,
            stdout='{"type":"result","is_error":false,"result":123}',
            stderr="",
        ),
        # is_error が true
        CommandResult(
            returncode=0,
            stdout='{"type":"result","is_error":true,"result":"{\\"title\\":\\"t\\",\\"n\\":1}"}',
            stderr="",
        ),
        # 本文から JSON オブジェクトを取り出せない
        CommandResult(
            returncode=0,
            stdout='{"type":"result","is_error":false,"result":"Hello, no JSON here."}',
            stderr="",
        ),
        # 取り出した JSON が output_type の検証に通らない
        CommandResult(
            returncode=0,
            stdout='{"type":"result","is_error":false,"result":"{\\"title\\": 123}"}',
            stderr="",
        ),
    ],
)
def test_generate_llm_output_error(tmp_path: Path, result: CommandResult) -> None:
    # AC-7
    fake = _FakeRunner(result)
    client = ClaudeCliClient(runner=fake, cwd=tmp_path)
    with pytest.raises(LlmOutputError) as excinfo:
        client.generate(
            session="my-session",
            step="my-step",
            model="claude-sonnet-5-5",
            system="SYS",
            user="USER",
            output_type=M,
        )
    assert excinfo.value.session == "my-session"
    assert excinfo.value.step == "my-step"


def test_no_anthropic_imports() -> None:
    # AC-10
    root = Path(__file__).resolve().parents[2]
    import_target = "import " + "anthropic"
    from_target = "from " + "anthropic"

    for path in list((root / "src").rglob("*.py")) + list(
        (root / "tests").rglob("*.py")
    ):
        content = path.read_text(encoding="utf-8")
        assert import_target not in content, f"Found in {path}"
        assert from_target not in content, f"Found in {path}"

    assert hasattr(llm_module, "AnthropicLlmClient") is False


def test_real_claude_command_forbidden_in_tests(tmp_path: Path) -> None:
    # AC-11
    runner = SubprocessRunner()
    with pytest.raises(RuntimeError):
        runner.run(["claude", "--version"], cwd=tmp_path)

    result = runner.run(["git", "--version"], cwd=tmp_path)
    assert result.returncode == 0
