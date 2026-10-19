import dataclasses
import datetime
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from auto_zenn_pipeline.errors import PublishError
from auto_zenn_pipeline.publish import PublishResult, publish
from auto_zenn_pipeline.runner import CommandResult, CommandRunner, SubprocessRunner
from auto_zenn_pipeline.session import SessionRef


class HybridRunner(CommandRunner):
    def __init__(self, gh_returncode: int = 0) -> None:
        self.gh_returncode = gh_returncode
        self.calls: list[tuple[list[str], Path]] = []
        self.gh_calls: list[list[str]] = []
        self.body_path: str | None = None
        self.body_at_call: str | None = None
        self._subprocess_runner = SubprocessRunner()

    def run(self, args: Sequence[str], *, cwd: Path) -> CommandResult:
        call_args = list(args)
        self.calls.append((call_args, cwd))
        if call_args and call_args[0] == "gh":
            self.gh_calls.append(call_args)
            if "--body-file" in call_args:
                idx = call_args.index("--body-file")
                if idx + 1 < len(call_args):
                    self.body_path = call_args[idx + 1]
                    with open(self.body_path, encoding="utf-8", newline="") as f:
                        self.body_at_call = f.read()
            return CommandResult(
                returncode=self.gh_returncode,
                stdout="  https://github.com/o/r/pull/1\n",
                stderr="" if self.gh_returncode == 0 else "gh error",
            )
        return self._subprocess_runner.run(call_args, cwd=cwd)


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, SessionRef]:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")

    origin_dir = tmp_path / "origin.git"
    origin_dir.mkdir()
    subprocess.run(["git", "init", "--bare", "-q"], cwd=origin_dir, check=True)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin_dir)],
        cwd=repo_dir,
        check=True,
    )

    session = SessionRef(
        repo_root=repo_dir,
        name="2026-01-01_abc",
        path=repo_dir / "raw_logs" / "2026-01-01_abc",
        date=datetime.date(2026, 1, 1),
        session_id="abc",
    )
    return repo_dir, origin_dir, session


def test_publish_result_dataclass() -> None:
    # AC-1
    fields = [f.name for f in dataclasses.fields(PublishResult)]
    assert fields == ["branch", "tag", "commit_sha", "pr_url"]
    res = PublishResult(
        branch="b",
        tag="t",
        commit_sha="c",
        pr_url="u",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.branch = "new"  # type: ignore[misc]


def test_publish_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-2 ~ AC-10
    repo_dir, origin_dir, session = _setup(tmp_path, monkeypatch)
    main_sha = _git(repo_dir, "rev-parse", "HEAD")

    slug = "my-article-slug"
    files = {
        f"articles/{slug}.md": "# タイトル\r\n\r\n本文です。\n",
        f"raw_logs/{session.name}/adr.json": '{"title": "決定事項", "detail": "日本語\r\n"}',
    }
    pr_title = f"draft: {slug} ({session.name})"
    pr_body = "## PR本文\r\n説明文です。"

    runner = HybridRunner()
    result = publish(session, slug, files, pr_title, pr_body, runner)

    # AC-8
    assert result.branch == f"article/{session.name}"
    assert result.tag == f"draft/{slug}"
    assert len(result.commit_sha) == 40
    assert result.pr_url == "https://github.com/o/r/pull/1"

    # AC-2
    branch_ref = f"refs/heads/article/{session.name}"
    tag_ref = f"refs/tags/draft/{slug}"
    origin_branch_sha = _git(origin_dir, "rev-parse", branch_ref)
    origin_tag_sha = _git(origin_dir, "rev-parse", tag_ref)
    assert origin_branch_sha == origin_tag_sha
    assert origin_tag_sha == result.commit_sha

    # AC-3
    tag_type = _git(origin_dir, "cat-file", "-t", tag_ref)
    assert tag_type == "commit"

    # AC-4
    parent_sha = _git(origin_dir, "rev-parse", f"{result.commit_sha}^")
    assert parent_sha == main_sha

    diff_lines = _git(
        origin_dir,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        result.commit_sha,
    ).splitlines()
    expected_diff = {
        f"A\tarticles/{slug}.md",
        f"A\traw_logs/{session.name}/adr.json",
    }
    assert set(diff_lines) == expected_diff
    assert len(diff_lines) == 2

    for path, content in files.items():
        show_res = subprocess.run(
            ["git", "show", f"{result.commit_sha}:{path}"],
            cwd=origin_dir,
            check=True,
            capture_output=True,
        )
        assert show_res.stdout == content.encode("utf-8")

    # AC-5
    log_format = _git(origin_dir, "log", "-1", "--format=%an|%ae|%s", result.commit_sha)
    assert (
        log_format
        == f"github-actions[bot]|41898282+github-actions[bot]@users.noreply.github.com|draft: {slug} ({session.name})"
    )

    # AC-6
    assert len(runner.gh_calls) == 1
    gh_args = runner.gh_calls[0]
    assert gh_args[0] == "gh"
    pr_idx = gh_args.index("pr")
    create_idx = gh_args.index("create")
    assert pr_idx < create_idx
    base_idx = gh_args.index("--base")
    assert gh_args[base_idx + 1] == "main"
    head_idx = gh_args.index("--head")
    assert gh_args[head_idx + 1] == f"article/{session.name}"
    title_idx = gh_args.index("--title")
    assert gh_args[title_idx + 1] == pr_title
    body_idx = gh_args.index("--body-file")
    assert gh_args[body_idx + 1] == runner.body_path
    assert runner.body_at_call == pr_body

    # AC-7
    assert runner.body_path is not None
    assert not Path(runner.body_path).exists()

    # AC-9
    for _call_args, cwd in runner.calls:
        assert cwd == session.repo_root

    # AC-10
    current_branch = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    assert current_branch == "main"


def test_publish_dirty_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-11
    repo_dir, _origin_dir, session = _setup(tmp_path, monkeypatch)
    untracked = repo_dir / "untracked.txt"
    untracked.write_text("hello", encoding="utf-8")

    slug = "dirty-slug"
    files = {
        f"articles/{slug}.md": "# Title",
        f"raw_logs/{session.name}/adr.json": "{}",
    }
    runner = HybridRunner()

    with pytest.raises(PublishError) as exc_info:
        publish(session, slug, files, "title", "body", runner)

    assert exc_info.value.session == session.name
    assert exc_info.value.command.startswith("git status")

    local_branches = _git(repo_dir, "branch", "--list", "article/*")
    assert local_branches == ""
    local_tags = _git(repo_dir, "tag", "--list", "draft/*")
    assert local_tags == ""

    origin_ls = _git(repo_dir, "ls-remote", "origin")
    assert "article/" not in origin_ls
    assert "draft/" not in origin_ls
    assert len(runner.gh_calls) == 0


def test_publish_gh_failure_switches_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-12
    repo_dir, _origin_dir, session = _setup(tmp_path, monkeypatch)
    slug = "gh-fail-slug"
    files = {
        f"articles/{slug}.md": "# Title",
        f"raw_logs/{session.name}/adr.json": "{}",
    }
    runner = HybridRunner(gh_returncode=1)

    with pytest.raises(PublishError) as exc_info:
        publish(session, slug, files, "title", "body", runner)

    assert exc_info.value.session == session.name
    assert exc_info.value.command.startswith("gh pr create")

    current_branch = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    assert current_branch == "main"

    assert runner.body_path is not None
    assert not Path(runner.body_path).exists()
