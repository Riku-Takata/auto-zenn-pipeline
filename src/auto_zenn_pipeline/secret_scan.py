"""Secret pattern scanning for session logs."""

import re
from dataclasses import dataclass

from auto_zenn_pipeline.errors import SecretDetectedError
from auto_zenn_pipeline.session import SessionLogs


@dataclass(frozen=True)
class SecretPattern:
    name: str
    regex: re.Pattern[str]


PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    SecretPattern("openai_api_key", re.compile(r"sk-(proj-)?[A-Za-z0-9_-]{20,}")),
    SecretPattern("aws_access_key_id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    SecretPattern(
        "github_token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")
    ),
    SecretPattern("github_token", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    SecretPattern("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    SecretPattern("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    SecretPattern("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

_CHAT_FILE = "chat_history.json"
_GIT_FILE = "git_context.json"


def _match(text: str) -> str | None:
    for p in PATTERNS:
        if p.regex.search(text) is not None:
            return p.name
    return None


def scan(logs: SessionLogs) -> None:
    for i, msg in enumerate(logs.chat.root):
        pattern_name = _match(msg.content)
        if pattern_name is not None:
            raise SecretDetectedError(
                session=logs.ref.name,
                file=_CHAT_FILE,
                location=f"messages[{i}]",
                pattern_name=pattern_name,
            )

    for i, commit in enumerate(logs.git.commits):
        pattern_name = _match(commit.message)
        if pattern_name is not None:
            raise SecretDetectedError(
                session=logs.ref.name,
                file=_GIT_FILE,
                location=f"commits[{i}].message",
                pattern_name=pattern_name,
            )
        if commit.diff is not None:
            pattern_name = _match(commit.diff)
            if pattern_name is not None:
                raise SecretDetectedError(
                    session=logs.ref.name,
                    file=_GIT_FILE,
                    location=f"commits[{i}].diff",
                    pattern_name=pattern_name,
                )
