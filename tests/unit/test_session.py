import datetime
import json
import os
import shutil
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path

import pytest

from auto_zenn_pipeline.errors import SessionError
from auto_zenn_pipeline.models import ChatHistory, GitContext
from auto_zenn_pipeline.session import (
    SessionLogs,
    SessionRef,
    load_session,
    resolve_session,
)

FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests/fixtures/raw_logs/2026-10-15_valid01"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    dest = tmp_path / "raw_logs" / "2026-10-15_valid01"
    shutil.copytree(FIXTURE, dest)
    return tmp_path


def make_session(
    repo_root: Path,
    name: str,
    chat_bytes: bytes | None = None,
    git_bytes: bytes | None = None,
) -> SessionRef:
    session_dir = repo_root / "raw_logs" / name
    session_dir.mkdir(parents=True, exist_ok=True)
    chat_file = session_dir / "chat_history.json"
    if chat_bytes is None:
        shutil.copyfile(FIXTURE / "chat_history.json", chat_file)
    else:
        chat_file.write_bytes(chat_bytes)
    if git_bytes is not None:
        (session_dir / "git_context.json").write_bytes(git_bytes)
    return resolve_session(repo_root, f"raw_logs/{name}")


def test_dataclass_fields() -> None:
    assert is_dataclass(SessionRef)
    assert is_dataclass(SessionLogs)
    assert [f.name for f in fields(SessionRef)] == [
        "repo_root",
        "name",
        "path",
        "date",
        "session_id",
    ]
    assert [f.name for f in fields(SessionLogs)] == ["ref", "chat", "git"]


def test_session_ref_is_frozen(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    with pytest.raises(FrozenInstanceError):
        ref.name = "x"  # type: ignore[misc]


def test_resolve_valid(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    assert ref.name == "2026-10-15_valid01"
    assert ref.date == datetime.date(2026, 10, 15)
    assert ref.session_id == "valid01"
    assert ref.repo_root == repo.resolve()
    assert ref.path == (repo / "raw_logs" / "2026-10-15_valid01").resolve()
    assert ref.path.is_absolute() is True


def test_resolve_trailing_slash(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01/")
    assert ref.name == "2026-10-15_valid01"
    assert ref.date == datetime.date(2026, 10, 15)
    assert ref.session_id == "valid01"
    assert ref.path == (repo / "raw_logs" / "2026-10-15_valid01").resolve()


def test_session_id_after_first_underscore(repo: Path) -> None:
    target_dir = repo / "raw_logs" / "2026-10-15_a_b"
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE / "chat_history.json", target_dir / "chat_history.json")
    ref = resolve_session(repo, "raw_logs/2026-10-15_a_b")
    assert ref.session_id == "a_b"
    assert ref.name == "2026-10-15_a_b"


def test_reject_absolute(repo: Path) -> None:
    abs_path = str((repo / "raw_logs" / "2026-10-15_valid01").resolve())
    with pytest.raises(SessionError) as exc_info:
        resolve_session(repo, abs_path)
    assert exc_info.value.session == abs_path
    assert exc_info.value.file is None


def test_reject_dotdot(repo: Path) -> None:
    path_str = "raw_logs/../raw_logs/2026-10-15_valid01"
    with pytest.raises(SessionError) as exc_info:
        resolve_session(repo, path_str)
    assert exc_info.value.session == path_str
    assert exc_info.value.file is None


def test_reject_symlink_outside(
    tmp_path_factory: pytest.TempPathFactory, repo: Path
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "2026-10-15_outside"
    shutil.copytree(FIXTURE, outside)
    link_path = repo / "raw_logs" / "2026-10-15_link"
    os.symlink(outside, link_path)
    with pytest.raises(SessionError) as exc_info:
        resolve_session(repo, "raw_logs/2026-10-15_link")
    assert exc_info.value.session == "raw_logs/2026-10-15_link"
    assert exc_info.value.file is None


def test_reject_nested(repo: Path) -> None:
    nested = repo / "raw_logs" / "a" / "2026-10-15_x"
    nested.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE / "chat_history.json", nested / "chat_history.json")
    with pytest.raises(SessionError) as exc_info:
        resolve_session(repo, "raw_logs/a/2026-10-15_x")
    assert exc_info.value.session == "raw_logs/a/2026-10-15_x"
    assert exc_info.value.file is None


@pytest.mark.parametrize(
    "bad_name",
    [
        "2026-1-15_x",
        "2026-13-01_x",
        "2026-10-15_",
        "2026-10-15_a.b",
    ],
)
def test_reject_bad_name(repo: Path, bad_name: str) -> None:
    bad_dir = repo / "raw_logs" / bad_name
    bad_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE / "chat_history.json", bad_dir / "chat_history.json")
    path_str = f"raw_logs/{bad_name}"
    with pytest.raises(SessionError) as exc_info:
        resolve_session(repo, path_str)
    assert exc_info.value.session == path_str
    assert exc_info.value.file is None


def test_reject_missing_dir(repo: Path) -> None:
    path_str = "raw_logs/2026-10-15_missing"
    with pytest.raises(SessionError) as exc_info:
        resolve_session(repo, path_str)
    assert exc_info.value.session == path_str
    assert exc_info.value.file is None


def test_reject_missing_chat(repo: Path) -> None:
    nochat = repo / "raw_logs" / "2026-10-15_nochat"
    nochat.mkdir(parents=True, exist_ok=True)
    path_str = "raw_logs/2026-10-15_nochat"
    with pytest.raises(SessionError) as exc_info:
        resolve_session(repo, path_str)
    assert exc_info.value.session == path_str
    assert exc_info.value.file == "chat_history.json"


def test_load_valid(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    logs = load_session(ref)
    assert logs.ref == ref
    assert isinstance(logs.chat, ChatHistory)
    assert len(logs.chat.root) == 4
    assert isinstance(logs.git, GitContext)
    assert len(logs.git.commits) == 2


def test_load_preserves_strings(repo: Path) -> None:
    ref = resolve_session(repo, "raw_logs/2026-10-15_valid01")
    logs = load_session(ref)
    assert (
        logs.chat.root[1].content
        == "次の 2 案があります。\n1. dataclass と手書きの検証\n2. pydantic のモデル"
    )
    assert logs.chat.root[2].content == ""
    assert logs.git.commits[0].sha == "4ee0603a1b2c3d4e5f60718293a4b5c6d7e8f901"
    assert logs.git.commits[1].diff is None


def test_load_without_git_context(repo: Path) -> None:
    no_git_dir = repo / "raw_logs" / "2026-10-15_nogit"
    no_git_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE / "chat_history.json", no_git_dir / "chat_history.json")
    ref = resolve_session(repo, "raw_logs/2026-10-15_nogit")
    logs = load_session(ref)
    assert logs.git.commits == []


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("bom", b"\xef\xbb\xbf" + (FIXTURE / "chat_history.json").read_bytes()),
        ("bad_json", b"[{"),
        ("empty_array", b"[]"),
        (
            "system_role",
            b'[{"role": "system", "content": "hello", "timestamp": "2026-10-15T10:00:00+09:00"}]',
        ),
        (
            "naive_timestamp",
            b'[{"role": "user", "content": "hello", "timestamp": "2026-10-15T10:00:00"}]',
        ),
        ("invalid_utf8", b"\xff\xfe\x00"),
    ],
)
def test_load_bad_chat(repo: Path, name: str, content: bytes) -> None:
    session_name = f"2026-10-15_bad_{name}"
    ref = make_session(repo, session_name, chat_bytes=content)
    with pytest.raises(SessionError) as exc_info:
        load_session(ref)
    assert exc_info.value.session == ref.name
    assert exc_info.value.file == "chat_history.json"


def test_load_bad_git_sha(repo: Path) -> None:
    session_name = "2026-10-15_badgit"
    bad_git_content = json.dumps(
        {
            "commits": [
                {
                    "sha": "XYZ",
                    "message": "msg",
                    "timestamp": "2026-10-15T10:00:00+09:00",
                }
            ]
        }
    ).encode("utf-8")
    ref = make_session(repo, session_name, git_bytes=bad_git_content)
    with pytest.raises(SessionError) as exc_info:
        load_session(ref)
    assert exc_info.value.session == ref.name
    assert exc_info.value.file == "git_context.json"
