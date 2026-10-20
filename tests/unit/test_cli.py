import inspect
import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from auto_zenn_pipeline.article import normalize_topics
from auto_zenn_pipeline.cli import main
from auto_zenn_pipeline.models import Adr
from auto_zenn_pipeline.runner import CommandResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "raw_logs" / "2026-10-15_valid01"
SESSION = "raw_logs/2026-10-15_valid01"
NAME = "2026-10-15_valid01"

ADR_OK: dict[str, Any] = {
    "worth_writing": True,
    "skip_reason": None,
    "context": "文脈",
    "options": [
        {
            "name": "A",
            "summary": "s",
            "proposed_by": "human",
            "rejected_reason": None,
        }
    ],
    "decision": {"choice": "A", "why": "w", "tradeoffs": ["t"]},
    "ai_steering": [],
}
ARTICLE: dict[str, Any] = {
    "title": "CLI の設計判断",
    "emoji": "🧭",
    "topics": ["Python", "CLI", "python"],
    "slug": "cli-design-decision",
    "body": "本文です。\n",
}


class FakeLlm:
    def __init__(self, adr: dict[str, Any] | None = None) -> None:
        self.adr = adr if adr is not None else ADR_OK
        self.calls: list[dict[str, Any]] = []

    def call_tool(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.adr if kwargs["step"] == "adr" else ARTICLE


class FailingRevParse:
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        input: str | None = None,
    ) -> CommandResult:
        return CommandResult(1, "", "fatal")


def _setup_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "Tester")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "test@example.com")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=tmp_path,
        check=True,
    )
    shutil.copy(PROJECT_ROOT / "pipeline.toml", tmp_path / "pipeline.toml")
    shutil.copytree(PROJECT_ROOT / "prompts", tmp_path / "prompts")
    shutil.copytree(FIXTURE, tmp_path / "raw_logs" / NAME)
    return tmp_path


def _set_content(repo: Path, value: str) -> None:
    path = repo / "raw_logs" / NAME / "chat_history.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[0]["content"] = value
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _md_files(repo: Path) -> list[Path]:
    return list((repo / "articles").glob("*.md"))


def test_signature() -> None:
    params = inspect.signature(main).parameters
    for key in ("llm", "runner", "repo_root"):
        assert params[key].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[key].default is None


def test_success_writes_article_and_adr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path, monkeypatch)
    fake = FakeLlm()
    code = main(["--session", SESSION, "--no-publish"], llm=fake, repo_root=repo)
    assert code == 0
    text = (repo / "articles" / "cli-design-decision.md").read_text(encoding="utf-8")
    front = text.split("---\n")[1].splitlines()
    meta = {k: json.loads(v) for k, v in (line.split(": ", 1) for line in front)}
    assert meta["title"] == ARTICLE["title"]
    assert meta["emoji"] == ARTICLE["emoji"]
    assert meta["type"] == "tech"
    assert meta["topics"] == normalize_topics(ARTICLE["topics"])
    assert meta["published"] is True
    adr = Adr.model_validate_json(
        (repo / "raw_logs" / NAME / "adr.json").read_text(encoding="utf-8")
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert adr.session == NAME
    assert adr.source_commit == head
    assert adr.model == "claude-sonnet-5-5"
    assert adr.schema_version == 1
    assert [c["step"] for c in fake.calls] == ["adr", "article"]


def test_skip_prints_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _setup_repo(tmp_path, monkeypatch)
    fake = FakeLlm({**ADR_OK, "worth_writing": False, "skip_reason": "a%b\r\nc"})
    code = main(["--session", SESSION, "--no-publish"], llm=fake, repo_root=repo)
    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    assert f"::notice title=skipped {NAME}::a%25b%0D%0Ac" in lines
    assert _md_files(repo) == []
    assert not (repo / "raw_logs" / NAME / "adr.json").exists()
    assert len(fake.calls) == 1


def test_secret_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _setup_repo(tmp_path, monkeypatch)
    secret = "sk-ant-" + "A" * 30
    _set_content(repo, secret)
    fake = FakeLlm()
    code = main(["--session", SESSION, "--no-publish"], llm=fake, repo_root=repo)
    assert code == 1
    cap = capsys.readouterr()
    assert f"::error title=SecretDetectedError {NAME}::" in cap.out
    assert fake.calls == []
    assert secret not in cap.out + cap.err


def test_log_too_large(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _setup_repo(tmp_path, monkeypatch)
    _set_content(repo, "x" * 300001)
    fake = FakeLlm()
    code = main(["--session", SESSION, "--no-publish"], llm=fake, repo_root=repo)
    assert code == 1
    assert f"::error title=LogTooLargeError {NAME}::" in capsys.readouterr().out
    assert fake.calls == []


def test_already_generated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _setup_repo(tmp_path, monkeypatch)
    adr_path = repo / "raw_logs" / NAME / "adr.json"
    adr_path.write_text("{}", encoding="utf-8")
    fake = FakeLlm()
    code = main(["--session", SESSION, "--no-publish"], llm=fake, repo_root=repo)
    assert code == 1
    assert f"::error title=AlreadyGeneratedError {NAME}::" in capsys.readouterr().out
    assert fake.calls == []
    assert adr_path.read_text(encoding="utf-8") == "{}"
    assert _md_files(repo) == []


def test_unresolved_session_uses_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _setup_repo(tmp_path, monkeypatch)
    path = "raw_logs/2026-10-15_nonexistent"
    fake = FakeLlm()
    code = main(["--session", path, "--no-publish"], llm=fake, repo_root=repo)
    assert code == 1
    assert f"::error title=SessionError {path}::" in capsys.readouterr().out
    assert fake.calls == []


def test_rev_parse_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _setup_repo(tmp_path, monkeypatch)
    code = main(
        ["--session", SESSION, "--no-publish"],
        llm=FakeLlm(),
        runner=FailingRevParse(),
        repo_root=repo,
    )
    assert code == 1
    assert f"::error title=PublishError {NAME}::" in capsys.readouterr().out
    assert _md_files(repo) == []
    assert not (repo / "raw_logs" / NAME / "adr.json").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["--session", SESSION, "--before", "abc1234", "--no-publish"],
        ["--no-publish"],
        [],
        ["--session", SESSION, "--after", "abc1234", "--no-publish"],
    ],
)
def test_argument_errors(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == 2
