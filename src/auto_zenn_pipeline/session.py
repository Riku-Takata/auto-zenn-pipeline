"""Session resolution and log loading."""

import datetime
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import (  # type: ignore[import-not-found,unused-ignore]
    BaseModel,
    ValidationError,
)

from auto_zenn_pipeline.errors import SessionError
from auto_zenn_pipeline.models import ChatHistory, GitContext

_RAW_LOGS = "raw_logs"
_CHAT_FILE = "chat_history.json"
_GIT_FILE = "git_context.json"
_NAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}_[A-Za-z0-9_-]+", re.ASCII)


@dataclass(frozen=True)
class SessionRef:
    repo_root: Path
    name: str
    path: Path
    date: datetime.date
    session_id: str


@dataclass(frozen=True)
class SessionLogs:
    ref: SessionRef
    chat: ChatHistory
    git: GitContext


def resolve_session(repo_root: Path, path: str) -> SessionRef:
    rel = Path(path)
    if rel.is_absolute():
        raise SessionError(session=path, file=None)
    if ".." in rel.parts:
        raise SessionError(session=path, file=None)

    root = repo_root.resolve()
    target = (root / rel).resolve()
    raw = (root / _RAW_LOGS).resolve()
    if target.parent != raw:
        raise SessionError(session=path, file=None)

    name = target.name
    if _NAME_RE.fullmatch(name) is None:
        raise SessionError(session=path, file=None)

    try:
        session_date = datetime.date.fromisoformat(name[:10])
    except ValueError as exc:
        raise SessionError(session=path, file=None) from exc

    if not target.is_dir():
        raise SessionError(session=path, file=None)

    if not (target / _CHAT_FILE).is_file():
        raise SessionError(session=path, file=_CHAT_FILE)

    session_id = name.split("_", 1)[1]
    return SessionRef(
        repo_root=root,
        name=name,
        path=target,
        date=session_date,
        session_id=session_id,
    )


def _load_json[M: BaseModel](ref: SessionRef, filename: str, model: type[M]) -> M:
    target_file = ref.path / filename
    try:
        with target_file.open(encoding="utf-8") as f:
            data = json.load(f)
        return cast(M, model.model_validate(data))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise SessionError(session=ref.name, file=filename) from exc


def load_session(ref: SessionRef) -> SessionLogs:
    chat = _load_json(ref, _CHAT_FILE, ChatHistory)
    git_file = ref.path / _GIT_FILE
    if not git_file.exists():
        git = GitContext(commits=[])
    else:
        git = _load_json(ref, _GIT_FILE, GitContext)
    return SessionLogs(ref=ref, chat=chat, git=git)
