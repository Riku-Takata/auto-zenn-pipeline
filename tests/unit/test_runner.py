from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from auto_zenn_pipeline.runner import CommandResult, CommandRunner, SubprocessRunner


def test_command_runner_protocol() -> None:
    # AC-9
    assert CommandRunner is not None


def test_command_result_fields() -> None:
    # AC-10
    result = CommandResult(returncode=1, stdout="a", stderr="b")
    assert result.returncode == 1
    assert result.stdout == "a"
    assert result.stderr == "b"


def test_command_result_frozen() -> None:
    # AC-11
    result = CommandResult(returncode=1, stdout="a", stderr="b")
    with pytest.raises(FrozenInstanceError):
        result.returncode = 0  # type: ignore[misc]


def test_git_version(tmp_path: Path) -> None:
    # AC-12
    runner = SubprocessRunner()
    result = runner.run(["git", "--version"], cwd=tmp_path)
    assert isinstance(result, CommandResult)
    assert result.returncode == 0
    assert "git version" in result.stdout


def test_tuple_args(tmp_path: Path) -> None:
    # AC-14
    runner = SubprocessRunner()
    result = runner.run(("git", "--version"), cwd=tmp_path)
    assert isinstance(result, CommandResult)
    assert result.returncode == 0
    assert "git version" in result.stdout


def test_nonzero_returncode_outside_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-13
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    runner = SubprocessRunner()
    result = runner.run(["git", "rev-parse", "HEAD"], cwd=tmp_path)
    assert isinstance(result, CommandResult)
    assert result.returncode != 0
