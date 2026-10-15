import dataclasses
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from auto_zenn_pipeline.errors import LogTooLargeError
from auto_zenn_pipeline.models import ChatHistory, ChatMessage, Commit, GitContext
from auto_zenn_pipeline.render import check_size, render_logs
from auto_zenn_pipeline.session import load_session, resolve_session

FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests/fixtures/raw_logs/2026-10-15_valid01"
)

EXPECTED_LINES = [
    "<chat>",
    '<message index="0" role="user" timestamp="2026-10-15T10:00:00+09:00">',
    "セッションのログを読む部分を pydantic で書きたい。",
    "</message>",
    '<message index="1" role="assistant" timestamp="2026-10-15T10:00:05+09:00">',
    "次の 2 案があります。",
    "1. dataclass と手書きの検証",
    "2. pydantic のモデル",
    "</message>",
    '<message index="2" role="user" timestamp="2026-10-15T10:01:00+09:00">',
    "",
    "</message>",
    '<message index="3" role="assistant" timestamp="2026-10-15T10:01:10+09:00">',
    "pydantic で進めます。",
    "</message>",
    "</chat>",
    "",
    "<commits>",
    '<commit sha="4ee0603a1b2c3d4e5f60718293a4b5c6d7e8f901" timestamp="2026-10-15T09:50:00+09:00">',
    "<message>models: add ChatHistory</message>",
    "<diff>--- a/src/models.py",
    "+++ b/src/models.py",
    "@@ -0,0 +1 @@",
    "+class ChatHistory: ...</diff>",
    "</commit>",
    '<commit sha="1a2b3c4" timestamp="2026-10-15T09:55:00+09:00">',
    "<message>docs: update README</message>",
    "</commit>",
    "</commits>",
]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    dest = tmp_path / "raw_logs" / "2026-10-15_valid01"
    shutil.copytree(FIXTURE, dest)
    return tmp_path


def test_render_fixture_exact(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    logs = load_session(ref)
    result = render_logs(logs)
    expected = "\n".join(EXPECTED_LINES)
    assert result == expected
    assert not result.endswith("\n")
    lines = result.split("\n")
    assert len(lines) == 29
    assert lines[10] == ""
    assert lines[16] == ""
    assert lines[17] == "<commits>"
    assert lines[20:24] == [
        "<diff>--- a/src/models.py",
        "+++ b/src/models.py",
        "@@ -0,0 +1 @@",
        "+class ChatHistory: ...</diff>",
    ]


def test_multiline_content_preserved(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    logs = load_session(ref)
    result = render_logs(logs)
    lines = result.split("\n")
    target = '<message index="1" role="assistant" timestamp="2026-10-15T10:00:05+09:00">'
    k = lines.index(target)
    assert lines[k + 1 : k + 4] == [
        "次の 2 案があります。",
        "1. dataclass と手書きの検証",
        "2. pydantic のモデル",
    ]
    assert lines[k + 4] == "</message>"


def test_empty_content_single_blank_line(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    logs = load_session(ref)
    result = render_logs(logs)
    lines = result.split("\n")
    target = '<message index="2" role="user" timestamp="2026-10-15T10:01:00+09:00">'
    k = lines.index(target)
    assert lines[k + 1] == ""
    assert lines[k + 2] == "</message>"


def test_commit_without_diff(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    logs = load_session(ref)
    result = render_logs(logs)
    lines = result.split("\n")
    start = lines.index('<commit sha="1a2b3c4" timestamp="2026-10-15T09:55:00+09:00">')
    end = lines.index("</commit>", start)
    commit_lines = lines[start:end]
    assert not any(line.startswith("<diff>") for line in commit_lines)
    msg_idx = lines.index("<message>docs: update README</message>", start)
    assert lines[msg_idx + 1] == "</commit>"


def test_empty_commits(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    logs = load_session(ref)
    logs_no_commits = dataclasses.replace(logs, git=GitContext(commits=[]))
    result = render_logs(logs_no_commits)
    lines = result.split("\n")
    assert lines[-2] == "<commits>"
    assert lines[-1] == "</commits>"


def test_no_escaping(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    logs = load_session(ref)
    custom_msg = ChatMessage(
        role="user",
        content='a < b & "c"',
        timestamp=datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone(timedelta(hours=9))),
    )
    custom_logs = dataclasses.replace(
        logs,
        chat=ChatHistory([custom_msg]),
    )
    result = render_logs(custom_logs)
    lines = result.split("\n")
    assert 'a < b & "c"' in lines
    assert "&lt;" not in result
    assert "&amp;" not in result
    assert "&quot;" not in result


def test_diff_empty_string(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    logs = load_session(ref)
    commit = Commit(
        sha="4ee0603a1b2c3d4e5f60718293a4b5c6d7e8f901",
        message="empty diff",
        timestamp=datetime(2026, 10, 15, 9, 50, 0, tzinfo=timezone(timedelta(hours=9))),
        diff="",
    )
    custom_logs = dataclasses.replace(logs, git=GitContext(commits=[commit]))
    result = render_logs(custom_logs)
    lines = result.split("\n")
    assert "<diff></diff>" in lines


def test_check_size_exact_limit() -> None:
    assert check_size("raw_logs/s1", "a" * 10, 10) is None


def test_check_size_below_limit() -> None:
    assert check_size("raw_logs/s1", "a" * 9, 10) is None


def test_check_size_over_limit() -> None:
    with pytest.raises(LogTooLargeError) as exc_info:
        check_size("raw_logs/s1", "a" * 11, 10)
    assert exc_info.value.session == "raw_logs/s1"
    assert exc_info.value.size == 11
    assert exc_info.value.limit == 10


def test_check_size_counts_code_points() -> None:
    assert check_size("s", "あ" * 10, 10) is None
    with pytest.raises(LogTooLargeError) as exc_info:
        check_size("s", "あ" * 11, 10)
    assert exc_info.value.session == "s"
    assert exc_info.value.size == 11
    assert exc_info.value.limit == 10
