import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from auto_zenn_pipeline.cli import main
from auto_zenn_pipeline.models import Adr
from auto_zenn_pipeline.runner import CommandResult, CommandRunner, SubprocessRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "raw_logs" / "2026-10-15_valid01"

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


class GhFakeRunner(CommandRunner):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.gh_calls: list[list[str]] = []
        self._subprocess_runner = SubprocessRunner()

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        input: str | None = None,
    ) -> CommandResult:
        call_args = list(args)
        self.calls.append(call_args)
        if call_args and call_args[0] == "gh":
            self.gh_calls.append(call_args)
            return CommandResult(
                returncode=0,
                stdout="https://example.com/pr/1\n",
                stderr="",
            )
        return self._subprocess_runner.run(call_args, cwd=cwd, input=input)


def _setup_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "Tester")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "test@example.com")


def test_push_no_publish_continues_after_secret_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    shutil.copy(PROJECT_ROOT / "pipeline.toml", repo / "pipeline.toml")
    shutil.copytree(PROJECT_ROOT / "prompts", repo / "prompts")
    shutil.copytree(FIXTURE, repo / "raw_logs" / "2026-10-15_valid01")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "commit 1", "-q"], cwd=repo, check=True)
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    shutil.copytree(FIXTURE, repo / "raw_logs" / "2026-10-15_valid02")
    secret_dir = repo / "raw_logs" / "2026-10-16_secret01"
    shutil.copytree(FIXTURE, secret_dir)
    chat_file = secret_dir / "chat_history.json"
    data = json.loads(chat_file.read_text(encoding="utf-8"))
    data[0]["content"] = "sk-ant-" + "a" * 30
    chat_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "commit 2", "-q"], cwd=repo, check=True)
    after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    fake = FakeLlm()
    code = main(
        ["--before", before, "--after", after, "--no-publish"],
        llm=fake,
        repo_root=repo,
    )
    assert code == 1

    valid02_adr = repo / "raw_logs" / "2026-10-15_valid02" / "adr.json"
    assert valid02_adr.exists()
    adr = Adr.model_validate_json(valid02_adr.read_text(encoding="utf-8"))
    assert adr.session == "2026-10-15_valid02"

    md_files = list((repo / "articles").glob("*.md"))
    assert len(md_files) >= 1

    out = capsys.readouterr().out
    assert any(
        line.startswith("::error title=SecretDetectedError 2026-10-16_secret01::")
        for line in out.splitlines()
    )

    valid01_adr = repo / "raw_logs" / "2026-10-15_valid01" / "adr.json"
    assert not valid01_adr.exists()


def test_push_publish_two_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(monkeypatch)
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "--bare", "-q"], cwd=origin, check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    shutil.copy(PROJECT_ROOT / "pipeline.toml", repo / "pipeline.toml")
    shutil.copytree(PROJECT_ROOT / "prompts", repo / "prompts")

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True
    )
    subprocess.run(["git", "push", "-u", "origin", "main", "-q"], cwd=repo, check=True)
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    shutil.copytree(FIXTURE, repo / "raw_logs" / "2026-10-15_valid02")
    shutil.copytree(FIXTURE, repo / "raw_logs" / "2026-10-15_valid03")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add two sessions", "-q"], cwd=repo, check=True
    )
    after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    fake = FakeLlm()
    runner = GhFakeRunner()
    code = main(
        ["--before", before, "--after", after],
        llm=fake,
        runner=runner,
        repo_root=repo,
    )
    assert code == 0

    heads = subprocess.run(
        ["git", "ls-remote", "--heads", str(origin)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "refs/heads/article/2026-10-15_valid02" in heads
    assert "refs/heads/article/2026-10-15_valid03" in heads

    tags = subprocess.run(
        ["git", "ls-remote", "--tags", str(origin)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tag_lines = [line for line in tags.splitlines() if "refs/tags/draft/" in line]
    assert len(tag_lines) == 2


def test_push_null_before_processes_added_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    shutil.copy(PROJECT_ROOT / "pipeline.toml", repo / "pipeline.toml")
    shutil.copytree(PROJECT_ROOT / "prompts", repo / "prompts")
    shutil.copytree(FIXTURE, repo / "raw_logs" / "2026-10-15_valid01")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init session", "-q"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    fake = FakeLlm()
    code = main(
        ["--before", "0" * 40, "--after", head, "--no-publish"],
        llm=fake,
        repo_root=repo,
    )
    assert code == 0
    assert (repo / "articles" / f"{ARTICLE['slug']}.md").exists()
    assert (repo / "raw_logs" / "2026-10-15_valid01" / "adr.json").exists()


def test_push_adr_only_commit_calls_no_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    shutil.copy(PROJECT_ROOT / "pipeline.toml", repo / "pipeline.toml")
    shutil.copytree(PROJECT_ROOT / "prompts", repo / "prompts")
    shutil.copytree(FIXTURE, repo / "raw_logs" / "2026-10-15_valid01")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init session", "-q"], cwd=repo, check=True)
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    (repo / "raw_logs" / "2026-10-15_valid01" / "adr.json").write_text(
        "{}", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add adr only", "-q"], cwd=repo, check=True)
    after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    fake = FakeLlm()
    code = main(
        ["--before", before, "--after", after, "--no-publish"],
        llm=fake,
        repo_root=repo,
    )
    assert code == 0
    assert len(fake.calls) == 0


def test_before_without_after_exits_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--before", "a" * 40])
    assert excinfo.value.code == 2


def test_after_only_exits_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--after", "a" * 40])
    assert excinfo.value.code == 2


def test_push_unknown_after_sha_returns_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    shutil.copy(PROJECT_ROOT / "pipeline.toml", repo / "pipeline.toml")
    shutil.copytree(PROJECT_ROOT / "prompts", repo / "prompts")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=repo, check=True)
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    fake = FakeLlm()
    unknown_after = "f" * 40
    code = main(
        ["--before", before, "--after", unknown_after, "--no-publish"],
        llm=fake,
        repo_root=repo,
    )
    assert code == 1
    out = capsys.readouterr().out
    expected_prefix = f"::error title=SessionError {before}..{unknown_after}::"
    assert any(line.startswith(expected_prefix) for line in out.splitlines())
    assert len(fake.calls) == 0
