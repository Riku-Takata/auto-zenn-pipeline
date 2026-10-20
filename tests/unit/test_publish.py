import datetime
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from auto_zenn_pipeline import publish, runner
from auto_zenn_pipeline.errors import PublishError
from auto_zenn_pipeline.models import Adr, AdrDecision, AdrOption
from auto_zenn_pipeline.publish import (
    CommandRunner,
    build_pr_body,
    remote_branch_exists,
    remote_tag_exists,
)
from auto_zenn_pipeline.runner import CommandResult, SubprocessRunner
from auto_zenn_pipeline.session import SessionRef


class FakeRunner:
    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], Path]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        input: str | None = None,
    ) -> CommandResult:
        self.calls.append((list(args), cwd))
        return CommandResult(
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def _setup_repo_with_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, SessionRef]:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_DEFAULT_BRANCH", "main")

    origin_dir = tmp_path / "origin.git"
    origin_dir.mkdir()
    subprocess.run(["git", "init", "--bare", "-q"], cwd=origin_dir, check=True)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
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
    return repo_dir, session


def test_command_runner_reexported() -> None:
    # AC-1
    assert CommandRunner is runner.CommandRunner
    assert publish.CommandRunner is runner.CommandRunner


def test_public_functions_importable() -> None:
    # AC-2
    assert callable(build_pr_body)
    assert callable(remote_branch_exists)
    assert callable(remote_tag_exists)


def test_remote_branch_exists_real_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-3, AC-4
    repo_dir, session = _setup_repo_with_origin(tmp_path, monkeypatch)
    runner_instance = SubprocessRunner()

    assert remote_branch_exists(session, runner_instance) is False

    subprocess.run(
        ["git", "branch", "article/2026-01-01_abc"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(
        ["git", "push", "origin", "article/2026-01-01_abc", "-q"],
        cwd=repo_dir,
        check=True,
    )

    assert remote_branch_exists(session, runner_instance) is True


def test_remote_tag_exists_real_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-5, AC-6
    repo_dir, session = _setup_repo_with_origin(tmp_path, monkeypatch)
    runner_instance = SubprocessRunner()
    slug = "my-slug"
    tag = f"draft/{slug}"

    assert remote_tag_exists(session, tag, runner_instance) is False

    subprocess.run(["git", "tag", tag], cwd=repo_dir, check=True)
    subprocess.run(["git", "push", "origin", tag, "-q"], cwd=repo_dir, check=True)

    assert remote_tag_exists(session, tag, runner_instance) is True


def test_remote_branch_exists_fake_runner_success(tmp_path: Path) -> None:
    # AC-7
    session = SessionRef(
        repo_root=tmp_path,
        name="2026-01-01_abc",
        path=tmp_path / "raw_logs" / "2026-01-01_abc",
        date=datetime.date(2026, 1, 1),
        session_id="abc",
    )
    fake_runner = FakeRunner(returncode=0)
    result = remote_branch_exists(session, fake_runner)

    assert result is True
    assert len(fake_runner.calls) == 1
    args, cwd = fake_runner.calls[0]
    expected_args = [
        "git",
        "ls-remote",
        "--exit-code",
        "--heads",
        "origin",
        f"refs/heads/article/{session.name}",
    ]
    assert list(args) == expected_args
    assert cwd == session.repo_root


def test_remote_branch_exists_fake_runner_not_found(tmp_path: Path) -> None:
    # AC-8
    session = SessionRef(
        repo_root=tmp_path,
        name="2026-01-01_abc",
        path=tmp_path / "raw_logs" / "2026-01-01_abc",
        date=datetime.date(2026, 1, 1),
        session_id="abc",
    )
    fake_runner = FakeRunner(returncode=2)
    assert remote_branch_exists(session, fake_runner) is False


def test_remote_tag_exists_fake_runner(tmp_path: Path) -> None:
    # AC-9
    session = SessionRef(
        repo_root=tmp_path,
        name="2026-01-01_abc",
        path=tmp_path / "raw_logs" / "2026-01-01_abc",
        date=datetime.date(2026, 1, 1),
        session_id="abc",
    )
    fake_runner = FakeRunner(returncode=0)
    result = remote_tag_exists(session, "draft/x", fake_runner)

    assert result is True
    assert len(fake_runner.calls) == 1
    args, cwd = fake_runner.calls[0]
    expected_args = [
        "git",
        "ls-remote",
        "--exit-code",
        "--tags",
        "origin",
        "refs/tags/draft/x",
    ]
    assert list(args) == expected_args
    assert cwd == session.repo_root

    fake_runner2 = FakeRunner(returncode=2)
    assert remote_tag_exists(session, "draft/x", fake_runner2) is False


def test_remote_branch_exists_error_raises_publish_error(tmp_path: Path) -> None:
    # AC-10
    session = SessionRef(
        repo_root=tmp_path,
        name="2026-01-01_abc",
        path=tmp_path / "raw_logs" / "2026-01-01_abc",
        date=datetime.date(2026, 1, 1),
        session_id="abc",
    )
    fake_runner = FakeRunner(returncode=128)
    expected_command = " ".join(
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            f"refs/heads/article/{session.name}",
        ]
    )
    with pytest.raises(PublishError) as exc_info:
        remote_branch_exists(session, fake_runner)
    assert exc_info.value.session == session.name
    assert exc_info.value.command == expected_command


def test_remote_tag_exists_error_raises_publish_error(tmp_path: Path) -> None:
    # AC-11
    session = SessionRef(
        repo_root=tmp_path,
        name="2026-01-01_abc",
        path=tmp_path / "raw_logs" / "2026-01-01_abc",
        date=datetime.date(2026, 1, 1),
        session_id="abc",
    )
    fake_runner = FakeRunner(returncode=128)
    expected_command = " ".join(
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--tags",
            "origin",
            "refs/tags/draft/x",
        ]
    )
    with pytest.raises(PublishError) as exc_info:
        remote_tag_exists(session, "draft/x", fake_runner)
    assert exc_info.value.session == session.name
    assert exc_info.value.command == expected_command


def test_build_pr_body_contents(tmp_path: Path) -> None:
    # AC-12
    session = SessionRef(
        repo_root=tmp_path,
        name="2026-01-01_abc",
        path=tmp_path / "raw_logs" / "2026-01-01_abc",
        date=datetime.date(2026, 1, 1),
        session_id="abc",
    )
    adr = Adr(
        schema_version=1,
        session=session.name,
        source_commit="0123456789abcdef0123456789abcdef01234567",
        model="claude-sonnet-5-5",
        generated_at=datetime.datetime.now(datetime.UTC),
        context="test context",
        options=[
            AdrOption(
                name="Option A",
                summary="Summary A",
                proposed_by="human",
                rejected_reason=None,
            ),
            AdrOption(
                name="Option B",
                summary="Summary B",
                proposed_by="ai",
                rejected_reason="Too complex",
            ),
        ],
        decision=AdrDecision(
            choice="Option A",
            why="Simpler",
            tradeoffs=["tradeoff 1"],
        ),
        ai_steering=[],
    )
    slug = "test-slug-123"
    article_model = "claude-sonnet-5-5-writer"

    body = build_pr_body(session, slug, adr, article_model)

    assert isinstance(body, str)
    assert f"raw_logs/{session.name}" in body
    assert slug in body
    assert f"draft/{slug}" in body
    assert adr.model in body
    assert article_model in body
    assert adr.decision.choice in body
    for opt in adr.options:
        assert opt.name in body
    assert "published: true" in body
