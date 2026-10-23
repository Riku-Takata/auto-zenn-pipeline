"""Unit tests for secret scanning."""

import datetime
import re
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path

import pytest

from auto_zenn_pipeline.errors import SecretDetectedError
from auto_zenn_pipeline.models import ChatHistory, ChatMessage, Commit, GitContext
from auto_zenn_pipeline.secret_scan import PATTERNS, SecretPattern, scan
from auto_zenn_pipeline.session import (
    SessionLogs,
    SessionRef,
    load_session,
    resolve_session,
)


def make_logs(
    contents: list[str],
    commits: list[tuple[str, str | None]] | None = None,
) -> SessionLogs:
    if commits is None:
        commits = []
    ts = datetime.datetime(2026, 10, 15, 12, 0, tzinfo=datetime.UTC)
    ref = SessionRef(
        repo_root=Path("/tmp"),
        name="2026-10-15_test01",
        path=Path("/tmp/raw_logs/2026-10-15_test01"),
        date=datetime.date(2026, 10, 15),
        session_id="test01",
    )
    chat = ChatHistory(
        [ChatMessage(role="user", content=c, timestamp=ts) for c in contents]
    )
    git_commits = [
        Commit(
            sha="a" * 40,
            message=m,
            timestamp=ts,
            diff=d,
        )
        for m, d in commits
    ]
    git = GitContext(commits=git_commits)
    return SessionLogs(ref=ref, chat=chat, git=git)


def test_public_api() -> None:
    assert is_dataclass(SecretPattern)
    field_names = [f.name for f in fields(SecretPattern)]
    assert field_names == ["name", "regex"]

    pattern = SecretPattern("test", re.compile("test"))
    with pytest.raises(FrozenInstanceError):
        pattern.name = "other"  # type: ignore[misc]

    assert isinstance(PATTERNS, tuple)
    for p in PATTERNS:
        assert isinstance(p, SecretPattern)
        assert isinstance(p.regex, re.Pattern)


def test_pattern_names_order() -> None:
    assert [p.name for p in PATTERNS] == [
        "anthropic_api_key",
        "openai_api_key",
        "aws_access_key_id",
        "github_token",
        "github_token",
        "slack_token",
        "google_api_key",
        "private_key",
    ]


@pytest.mark.parametrize(
    ("secret", "expected_name"),
    [
        ("sk-ant-" + "a" * 24, "anthropic_api_key"),
        ("sk-" + "a" * 24, "openai_api_key"),
        ("sk-proj-" + "A" * 24, "openai_api_key"),
        ("AKIA" + "ABCDEFGHIJKLMNOP", "aws_access_key_id"),
        ("ghp_" + "a" * 36, "github_token"),
        ("github_pat_" + "A" * 22, "github_token"),
        ("xoxb-" + "1234567890", "slack_token"),
        ("AIza" + "A" * 35, "google_api_key"),
        ("-----BEGIN RSA " + "PRIVATE KEY-----", "private_key"),
        ("-----BEGIN " + "PRIVATE KEY-----", "private_key"),
    ],
)
def test_positive(
    secret: str,
    expected_name: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    logs = make_logs(["key: " + secret + " end", "これはふつうの日本語の文です。"])
    with pytest.raises(SecretDetectedError) as exc_info:
        scan(logs)

    exc = exc_info.value
    assert exc.pattern_name == expected_name
    assert exc.session == "2026-10-15_test01"

    if secret.startswith("sk-ant-"):
        assert exc.pattern_name != "openai_api_key"

    assert secret not in str(exc)
    assert secret not in repr(exc)
    for arg in exc.args:
        assert secret not in str(arg)

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


@pytest.mark.parametrize("i", [0, 2])
def test_chat_location(i: int) -> None:
    contents = ["通常テキスト0", "通常テキスト1", "通常テキスト2"]
    secret = "sk-ant-" + "a" * 24
    contents[i] = "key: " + secret + " end"
    logs = make_logs(contents)
    with pytest.raises(SecretDetectedError) as exc_info:
        scan(logs)
    assert exc_info.value.file == "chat_history.json"
    assert exc_info.value.location == f"messages[{i}]"


def test_commit_message_location() -> None:
    secret = "sk-ant-" + "a" * 24
    commits: list[tuple[str, str | None]] = [
        ("clean commit 0", "clean diff 0"),
        ("key: " + secret + " end", "clean diff 1"),
    ]
    logs = make_logs(["clean chat"], commits=commits)
    with pytest.raises(SecretDetectedError) as exc_info:
        scan(logs)
    assert exc_info.value.file == "git_context.json"
    assert exc_info.value.location == "commits[1].message"


def test_commit_diff_location() -> None:
    secret = "sk-ant-" + "a" * 24
    commits: list[tuple[str, str | None]] = [
        ("clean commit 0", "clean diff 0"),
        ("clean commit 1", "key: " + secret + " end"),
    ]
    logs = make_logs(["clean chat"], commits=commits)
    with pytest.raises(SecretDetectedError) as exc_info:
        scan(logs)
    assert exc_info.value.file == "git_context.json"
    assert exc_info.value.location == "commits[1].diff"


def test_none_diff_skipped() -> None:
    logs_no_secret = make_logs(["clean chat"], commits=[("clean commit 0", None)])
    scan(logs_no_secret)

    secret = "sk-ant-" + "a" * 24
    commits: list[tuple[str, str | None]] = [
        ("clean commit 0", None),
        ("key: " + secret + " end", "clean diff 1"),
    ]
    logs_with_secret = make_logs(["clean chat"], commits=commits)
    with pytest.raises(SecretDetectedError) as exc_info:
        scan(logs_with_secret)
    assert exc_info.value.location == "commits[1].message"


@pytest.mark.parametrize(
    ("contents", "commits", "expected_location"),
    [
        (
            [
                "clean",
                "key: " + ("sk-ant-" + "a" * 24) + " end",
                "clean",
                "key: " + ("sk-ant-" + "a" * 24) + " end",
            ],
            None,
            "messages[1]",
        ),
        (
            [
                "clean",
                "clean",
                "key: " + ("sk-ant-" + "a" * 24) + " end",
            ],
            [("key: " + ("sk-ant-" + "a" * 24) + " end", "clean diff")],
            "messages[2]",
        ),
        (
            ["clean"],
            [
                (
                    "key: " + ("sk-ant-" + "a" * 24) + " end",
                    "key: " + ("sk-ant-" + "a" * 24) + " end",
                )
            ],
            "commits[0].message",
        ),
        (
            ["clean"],
            [
                ("clean", "key: " + ("sk-ant-" + "a" * 24) + " end"),
                ("key: " + ("sk-ant-" + "a" * 24) + " end", "clean diff"),
            ],
            "commits[0].diff",
        ),
    ],
)
def test_first_match_wins(
    contents: list[str],
    commits: list[tuple[str, str | None]] | None,
    expected_location: str,
) -> None:
    logs = make_logs(contents, commits)
    with pytest.raises(SecretDetectedError) as exc_info:
        scan(logs)
    assert exc_info.value.location == expected_location


@pytest.mark.parametrize(
    "content",
    [
        "key: sk-abc end",
        "key: " + "AKIA" + "ABCDEFGHIJKLMNO" + " end",
        "これはふつうの日本語の文です。",
    ],
)
def test_negative(content: str) -> None:
    logs = make_logs([content])
    scan(logs)


def test_fixture_has_no_secret() -> None:
    fixture_root = Path(__file__).resolve().parents[2] / "tests/fixtures"
    session_ref = resolve_session(fixture_root, "raw_logs/2026-10-15_valid01")
    logs = load_session(session_ref)
    scan(logs)
