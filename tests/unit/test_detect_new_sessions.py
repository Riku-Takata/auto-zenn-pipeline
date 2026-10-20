import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from auto_zenn_pipeline.errors import SessionError
from auto_zenn_pipeline.runner import CommandResult, SubprocessRunner
from auto_zenn_pipeline.session import detect_new_sessions


class FakeRunner:
    def __init__(
        self, responses: dict[tuple[str, str], CommandResult] | None = None
    ) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[list[str], Path]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        input: str | None = None,
    ) -> CommandResult:
        self.calls.append((list(args), cwd))
        key = (args[0], args[1])
        if key in self.responses:
            return self.responses[key]
        return CommandResult(returncode=0, stdout="", stderr="")


def test_returns_sorted_sessions(tmp_path: Path) -> None:
    # AC-1, AC-2
    before = "a" * 40
    after = "b" * 40
    runner = FakeRunner(
        {
            ("git", "diff"): CommandResult(
                returncode=0,
                stdout="raw_logs/2026-10-15_bbb/chat_history.json\nraw_logs/2026-10-14_aaa/chat_history.json\n",
                stderr="",
            )
        }
    )
    result = detect_new_sessions(tmp_path, before, after, runner)
    assert result == ["raw_logs/2026-10-14_aaa", "raw_logs/2026-10-15_bbb"]


def test_diff_called_once_with_args(tmp_path: Path) -> None:
    # AC-3
    before = "a" * 40
    after = "b" * 40
    runner = FakeRunner(
        {
            ("git", "diff"): CommandResult(
                returncode=0,
                stdout="raw_logs/2026-10-15_bbb/chat_history.json\nraw_logs/2026-10-14_aaa/chat_history.json\n",
                stderr="",
            )
        }
    )
    detect_new_sessions(tmp_path, before, after, runner)
    assert len(runner.calls) == 1
    assert runner.calls[0] == (
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=A",
            "a" * 40,
            "b" * 40,
            "--",
            "raw_logs/*/chat_history.json",
        ],
        tmp_path,
    )


def test_null_before_uses_empty_tree(tmp_path: Path) -> None:
    # AC-4
    before = "0" * 40
    after = "b" * 40
    runner = FakeRunner(
        {
            ("git", "hash-object"): CommandResult(
                returncode=0,
                stdout="4b825dc642cb6eb9a060e54bf8d69288fbee4904\n",
                stderr="",
            ),
            ("git", "diff"): CommandResult(returncode=0, stdout="", stderr=""),
        }
    )
    detect_new_sessions(tmp_path, before, after, runner)
    assert len(runner.calls) == 2
    assert runner.calls[0] == (
        ["git", "hash-object", "-t", "tree", "/dev/null"],
        tmp_path,
    )
    assert runner.calls[1] == (
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=A",
            "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
            after,
            "--",
            "raw_logs/*/chat_history.json",
        ],
        tmp_path,
    )


def test_ignores_non_chat_and_nested(tmp_path: Path) -> None:
    # AC-5
    runner = FakeRunner(
        {
            ("git", "diff"): CommandResult(
                returncode=0,
                stdout="raw_logs/x/adr.json\nraw_logs/a/b/chat_history.json\n",
                stderr="",
            )
        }
    )
    assert detect_new_sessions(tmp_path, "a" * 40, "b" * 40, runner) == []


def test_empty_output(tmp_path: Path) -> None:
    # AC-6
    runner = FakeRunner(
        {("git", "diff"): CommandResult(returncode=0, stdout="", stderr="")}
    )
    assert detect_new_sessions(tmp_path, "a" * 40, "b" * 40, runner) == []


def test_dedup_and_blank_lines(tmp_path: Path) -> None:
    # AC-7
    runner = FakeRunner(
        {
            ("git", "diff"): CommandResult(
                returncode=0,
                stdout="raw_logs/s1/chat_history.json\n\nraw_logs/s1/chat_history.json\n",
                stderr="",
            )
        }
    )
    assert detect_new_sessions(tmp_path, "a" * 40, "b" * 40, runner) == ["raw_logs/s1"]


def test_ignores_other_prefix_and_filename(tmp_path: Path) -> None:
    # AC-8
    runner = FakeRunner(
        {
            ("git", "diff"): CommandResult(
                returncode=0,
                stdout="other/s1/chat_history.json\nraw_logs/s1/chat.json\n",
                stderr="",
            )
        }
    )
    assert detect_new_sessions(tmp_path, "a" * 40, "b" * 40, runner) == []


def test_no_name_validation(tmp_path: Path) -> None:
    # AC-9
    runner = FakeRunner(
        {
            ("git", "diff"): CommandResult(
                returncode=0,
                stdout="raw_logs/not-a-date/chat_history.json\n",
                stderr="",
            )
        }
    )
    assert detect_new_sessions(tmp_path, "a" * 40, "b" * 40, runner) == [
        "raw_logs/not-a-date"
    ]


def test_diff_failure_raises(tmp_path: Path) -> None:
    # AC-10
    before = "a" * 40
    after = "b" * 40
    runner = FakeRunner(
        {
            ("git", "diff"): CommandResult(
                returncode=1,
                stdout="",
                stderr="error",
            )
        }
    )
    with pytest.raises(SessionError) as exc_info:
        detect_new_sessions(tmp_path, before, after, runner)
    assert exc_info.value.session == f"{before}..{after}"
    assert exc_info.value.file is None


def test_hash_object_failure_raises(tmp_path: Path) -> None:
    # AC-11
    before = "0" * 40
    after = "b" * 40
    runner = FakeRunner(
        {
            ("git", "hash-object"): CommandResult(
                returncode=1,
                stdout="",
                stderr="error",
            )
        }
    )
    with pytest.raises(SessionError) as exc_info:
        detect_new_sessions(tmp_path, before, after, runner)
    assert exc_info.value.session == f"{before}..{after}"
    assert exc_info.value.file is None
    assert len(runner.calls) == 1


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _setup_git_repo(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, str]:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    _git(repo, "init", "-q")
    _git(repo, "commit", "--allow-empty", "-m", "A")
    commit_a = _git(repo, "rev-parse", "HEAD")

    s1_chat = repo / "raw_logs" / "2026-10-15_s1" / "chat_history.json"
    s1_chat.parent.mkdir(parents=True, exist_ok=True)
    s1_chat.write_text('{"messages": []}', encoding="utf-8")
    _git(repo, "add", "raw_logs/2026-10-15_s1/chat_history.json")
    _git(repo, "commit", "-m", "B")
    commit_b = _git(repo, "rev-parse", "HEAD")

    s1_adr = repo / "raw_logs" / "2026-10-15_s1" / "adr.json"
    s1_adr.write_text('{"adr": "ok"}', encoding="utf-8")
    _git(repo, "add", "raw_logs/2026-10-15_s1/adr.json")
    _git(repo, "commit", "-m", "C")
    commit_c = _git(repo, "rev-parse", "HEAD")

    return commit_a, commit_b, commit_c


def test_real_git_detects_added_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-12
    commit_a, commit_b, commit_c = _setup_git_repo(tmp_path, monkeypatch)
    runner = SubprocessRunner()
    assert detect_new_sessions(tmp_path, commit_a, commit_b, runner) == [
        "raw_logs/2026-10-15_s1"
    ]
    assert detect_new_sessions(tmp_path, commit_b, commit_c, runner) == []


def test_real_git_null_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-13
    _, commit_b, _ = _setup_git_repo(tmp_path, monkeypatch)
    runner = SubprocessRunner()
    assert detect_new_sessions(tmp_path, "0" * 40, commit_b, runner) == [
        "raw_logs/2026-10-15_s1"
    ]
