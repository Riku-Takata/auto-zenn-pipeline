import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from auto_zenn_pipeline.article import fallback_slug, normalize_topics
from auto_zenn_pipeline.cli import main
from auto_zenn_pipeline.models import Adr
from auto_zenn_pipeline.runner import CommandResult, CommandRunner, SubprocessRunner
from auto_zenn_pipeline.session import resolve_session

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


def _setup_publish_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "Tester")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "test@example.com")

    origin_dir = tmp_path / "origin.git"
    origin_dir.mkdir()
    subprocess.run(["git", "init", "--bare", "-q"], cwd=origin_dir, check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)

    shutil.copy(PROJECT_ROOT / "pipeline.toml", repo / "pipeline.toml")
    shutil.copytree(PROJECT_ROOT / "prompts", repo / "prompts")
    shutil.copytree(FIXTURE, repo / "raw_logs" / NAME)

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin_dir)], cwd=repo, check=True
    )
    subprocess.run(["git", "push", "-u", "origin", "main", "-q"], cwd=repo, check=True)

    return repo, origin_dir


def test_publish_flow_ac1_to_ac5_and_ac9(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, origin = _setup_publish_repo(tmp_path, monkeypatch)
    fake = FakeLlm()
    runner = GhFakeRunner()

    code = main(["--session", SESSION], llm=fake, runner=runner, repo_root=repo)
    assert code == 0

    head_check = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/article/{NAME}"],
        cwd=origin,
        capture_output=True,
        text=True,
    )
    assert head_check.returncode == 0

    tag_check = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/draft/{ARTICLE['slug']}"],
        cwd=origin,
        capture_output=True,
        text=True,
    )
    assert tag_check.returncode == 0

    art_text = subprocess.run(
        [
            "git",
            "show",
            f"refs/tags/draft/{ARTICLE['slug']}:articles/{ARTICLE['slug']}.md",
        ],
        cwd=origin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    front = art_text.split("---\n")[1].splitlines()
    meta = {k: json.loads(v) for k, v in (line.split(": ", 1) for line in front)}
    assert meta["title"] == ARTICLE["title"]
    assert meta["emoji"] == ARTICLE["emoji"]
    assert meta["type"] == "tech"
    assert meta["topics"] == normalize_topics(ARTICLE["topics"])
    assert meta["published"] is True

    adr_raw = subprocess.run(
        [
            "git",
            "show",
            f"refs/tags/draft/{ARTICLE['slug']}:raw_logs/{NAME}/adr.json",
        ],
        cwd=origin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    adr = Adr.model_validate_json(adr_raw)
    assert adr.session == NAME

    assert len(runner.gh_calls) == 1
    gh_cmd = runner.gh_calls[0]
    assert gh_cmd[1:3] == ["pr", "create"]
    assert "--base" in gh_cmd
    base_idx = gh_cmd.index("--base")
    assert gh_cmd[base_idx + 1] == "main"
    assert "--title" in gh_cmd
    title_idx = gh_cmd.index("--title")
    assert gh_cmd[title_idx + 1] == f"[draft] {ARTICLE['title']}"

    cur_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert cur_branch == "main"


def test_publish_already_generated_remote_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, origin = _setup_publish_repo(tmp_path, monkeypatch)
    subprocess.run(["git", "branch", f"article/{NAME}"], cwd=repo, check=True)
    subprocess.run(["git", "push", "origin", f"article/{NAME}"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "-D", f"article/{NAME}"], cwd=repo, check=True)

    fake = FakeLlm()
    runner = GhFakeRunner()

    code = main(["--session", SESSION], llm=fake, runner=runner, repo_root=repo)
    assert code == 1
    out = capsys.readouterr().out
    assert any(
        line.startswith(f"::error title=AlreadyGeneratedError {NAME}::")
        for line in out.splitlines()
    )
    assert len(fake.calls) == 0
    assert len(runner.gh_calls) == 0
    tags = subprocess.run(
        ["git", "tag", "-l", "draft/*"],
        cwd=origin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert tags == ""


def test_publish_skip_worth_writing_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, origin = _setup_publish_repo(tmp_path, monkeypatch)
    fake = FakeLlm({**ADR_OK, "worth_writing": False, "skip_reason": "not needed"})
    runner = GhFakeRunner()

    code = main(["--session", SESSION], llm=fake, runner=runner, repo_root=repo)
    assert code == 0

    forbidden = {"switch", "add", "commit", "tag", "push"}
    for cmd in runner.calls:
        if cmd and cmd[0] == "git":
            assert not any(sub in forbidden for sub in cmd[1:])
    assert len(runner.gh_calls) == 0

    assert list((repo / "articles").glob("*.md")) == []
    assert not (repo / "raw_logs" / NAME / "adr.json").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert status == ""


def test_publish_remote_tag_exists_uses_fallback_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, origin = _setup_publish_repo(tmp_path, monkeypatch)
    subprocess.run(["git", "tag", "draft/cli-design-decision"], cwd=repo, check=True)
    subprocess.run(
        ["git", "push", "origin", "draft/cli-design-decision"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "tag", "-d", "draft/cli-design-decision"], cwd=repo, check=True
    )

    fake = FakeLlm()
    runner = GhFakeRunner()

    ref = resolve_session(repo, SESSION)
    fb = fallback_slug(ref)

    code = main(["--session", SESSION], llm=fake, runner=runner, repo_root=repo)
    assert code == 0

    tag_check = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/draft/{fb}"],
        cwd=origin,
        capture_output=True,
        text=True,
    )
    assert tag_check.returncode == 0

    fb_md = subprocess.run(
        ["git", "show", f"refs/tags/draft/{fb}:articles/{fb}.md"],
        cwd=origin,
        capture_output=True,
        text=True,
    )
    assert fb_md.returncode == 0

    orig_md = subprocess.run(
        ["git", "show", f"refs/tags/draft/{fb}:articles/cli-design-decision.md"],
        cwd=origin,
        capture_output=True,
        text=True,
    )
    assert orig_md.returncode != 0


def test_no_publish_does_not_call_remote_or_publish_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, origin = _setup_publish_repo(tmp_path, monkeypatch)
    fake = FakeLlm()
    runner = GhFakeRunner()

    code = main(
        ["--session", SESSION, "--no-publish"], llm=fake, runner=runner, repo_root=repo
    )
    assert code == 0

    for cmd in runner.calls:
        if cmd and cmd[0] == "git":
            assert "ls-remote" not in cmd
            assert "switch" not in cmd
            assert "push" not in cmd
    assert len(runner.gh_calls) == 0

    assert (repo / "articles" / "cli-design-decision.md").exists()
    assert (repo / "raw_logs" / NAME / "adr.json").exists()
