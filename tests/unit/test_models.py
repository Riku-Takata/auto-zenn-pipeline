import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auto_zenn_pipeline.models import (
    Adr,
    AdrDecision,
    AdrExtraction,
    AdrOption,
    AiSteering,
    ArticleDraft,
    ChatHistory,
    ChatMessage,
    Commit,
    GitContext,
    export_json_schemas,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/fixtures/raw_logs/2026-10-15_valid01"
TS = "2026-10-15T09:50:00+09:00"

_EXPORTED = (AdrDecision, AdrOption, AiSteering, ChatMessage)


def _chat() -> ChatHistory:
    return ChatHistory.model_validate_json((FIXTURE / "chat_history.json").read_bytes())


def _git() -> GitContext:
    return GitContext.model_validate_json((FIXTURE / "git_context.json").read_bytes())


def test_fixture_chat_history_loads() -> None:
    assert _chat()


def test_fixture_git_context_loads() -> None:
    assert _git()


def test_fixture_chat_history_shape() -> None:
    root = _chat().root
    assert len(root) >= 4
    for i, m in enumerate(root):
        assert m.role == ("user" if i % 2 == 0 else "assistant")
        assert m.timestamp.tzinfo is not None
    assert any("\n" in m.content for m in root)
    assert any(m.content == "" for m in root)


def test_fixture_git_context_shape() -> None:
    commits = _git().commits
    assert any(c.diff for c in commits)
    raw = json.loads((FIXTURE / "git_context.json").read_text(encoding="utf-8"))
    omitted = {c["sha"] for c in raw["commits"] if "diff" not in c}
    assert any(c.sha in omitted and c.diff is None for c in commits)


def test_fixture_has_no_secret_patterns() -> None:
    patterns = [
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"sk-(proj-)?[A-Za-z0-9_-]{20,}",
        r"\b(AKIA|ASIA)[0-9A-Z]{16}\b",
        r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b",
        r"github_pat_[A-Za-z0-9_]{22,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"AIza[0-9A-Za-z_-]{35}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ]
    texts = [m.content for m in _chat().root]
    for c in _git().commits:
        texts.append(c.message)
        if c.diff is not None:
            texts.append(c.diff)
    for p in patterns:
        for t in texts:
            assert re.search(p, t) is None


def test_chat_history_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        ChatHistory.model_validate_json("[]")


def test_chat_history_rejects_system_role() -> None:
    with pytest.raises(ValidationError):
        ChatHistory.model_validate_json(
            '[{"role":"system","content":"x","timestamp":"2026-10-15T10:00:00+09:00"}]'
        )


def test_chat_history_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        ChatHistory.model_validate_json(
            '[{"role":"user","content":"x","timestamp":"2026-10-15T10:00:00"}]'
        )


def test_chat_history_accepts_empty_content() -> None:
    h = ChatHistory.model_validate_json(
        '[{"role":"assistant","content":"","timestamp":"2026-10-15T10:00:00+09:00"}]'
    )
    assert h.root[0].content == ""


@pytest.mark.parametrize("sha", ["abcdef0", "0" * 40])
def test_commit_sha_valid(sha: str) -> None:
    Commit.model_validate({"sha": sha, "message": "m", "timestamp": TS})


@pytest.mark.parametrize("sha", ["abcdef", "ABCDEF0", "a" * 41])
def test_commit_sha_invalid(sha: str) -> None:
    with pytest.raises(ValidationError):
        Commit.model_validate({"sha": sha, "message": "m", "timestamp": TS})


def test_commit_diff_default_null_and_value() -> None:
    base: dict[str, Any] = {"sha": "abcdef0", "message": "m", "timestamp": TS}
    assert Commit.model_validate(base).diff is None
    assert Commit.model_validate({**base, "diff": None}).diff is None
    assert Commit.model_validate({**base, "diff": "x"}).diff == "x"


def test_commit_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        Commit.model_validate(
            {"sha": "abcdef0", "message": "m", "timestamp": "2026-10-15T09:50:00"}
        )


def test_git_context_empty_object() -> None:
    assert GitContext.model_validate_json("{}").commits == []


def test_extra_keys_ignored_chat() -> None:
    h = ChatHistory.model_validate(
        [{"role": "user", "content": "x", "timestamp": TS, "extra_key": 1}]
    )
    assert "extra_key" not in h.model_dump()[0]
    assert not hasattr(h.root[0], "extra_key")


def test_extra_keys_ignored_git() -> None:
    g = GitContext.model_validate(
        {
            "extra_key": 1,
            "commits": [
                {"sha": "abcdef0", "message": "m", "timestamp": TS, "extra_key": 1}
            ],
        }
    )
    dumped = g.model_dump()
    assert "extra_key" not in dumped
    assert "extra_key" not in dumped["commits"][0]
    assert not hasattr(g, "extra_key")
    assert not hasattr(g.commits[0], "extra_key")


def _adr_input() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session": "s",
        "source_commit": "c",
        "model": "m",
        "generated_at": "2026-10-15T12:00:00+00:00",
        "options": [{"proposed_by": "ai", "rejected_reason": None}],
        "decision": {"tradeoffs": []},
        "ai_steering": [],
    }


def test_adr_field_order() -> None:
    assert list(Adr.model_fields) == [
        "schema_version",
        "session",
        "source_commit",
        "model",
        "generated_at",
        "context",
        "options",
        "decision",
        "ai_steering",
    ]


def test_adr_valid() -> None:
    Adr.model_validate(_adr_input())
    full_input = {
        **_adr_input(),
        "context": "some context",
        "options": [
            {
                "name": "opt1",
                "summary": "summary1",
                "proposed_by": "ai",
                "rejected_reason": "none",
            }
        ],
        "decision": {
            "choice": "choice1",
            "why": "why1",
            "tradeoffs": ["t1", "t2"],
        },
    }
    Adr.model_validate(full_input)


@pytest.mark.parametrize("change", ["version", "proposed_by", "naive"])
def test_adr_invalid(change: str) -> None:
    d = copy.deepcopy(_adr_input())
    if change == "version":
        d["schema_version"] = 2
    elif change == "proposed_by":
        d["options"][0]["proposed_by"] = "bot"
    else:
        d["generated_at"] = "2026-10-15T12:00:00"
    with pytest.raises(ValidationError):
        Adr.model_validate(d)


def test_adr_extraction_no_invariant() -> None:
    AdrExtraction.model_validate(
        {
            "worth_writing": False,
            "skip_reason": None,
            "context": "",
            "options": [],
            "decision": {"choice": "", "why": "", "tradeoffs": []},
            "ai_steering": [],
        }
    )


def test_article_draft_accepts_empty() -> None:
    ArticleDraft.model_validate(
        {"title": "", "emoji": "", "topics": [], "slug": "", "body": ""}
    )


def test_export_keys() -> None:
    assert set(export_json_schemas()) == {
        "chat_history.schema.json",
        "git_context.schema.json",
        "adr.schema.json",
    }


def test_export_values_match_model_schema() -> None:
    out = export_json_schemas()
    for name, m in (
        ("chat_history.schema.json", ChatHistory),
        ("git_context.schema.json", GitContext),
        ("adr.schema.json", Adr),
    ):
        expected = json.dumps(m.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        assert out[name] == expected


def _assert_schemas_match() -> None:
    schemas = REPO_ROOT / "schemas"
    expected = export_json_schemas()
    assert sorted(p.name for p in schemas.iterdir()) == sorted(expected)
    for name, value in expected.items():
        assert (schemas / name).read_text(encoding="utf-8") == value


def test_committed_schemas_match() -> None:
    _assert_schemas_match()


def test_export_script_from_repo_root() -> None:
    r = subprocess.run(
        [sys.executable, "scripts/export_schemas.py"], cwd=REPO_ROOT, check=False
    )
    assert r.returncode == 0
    _assert_schemas_match()


def test_export_script_from_other_cwd(tmp_path: Path) -> None:
    script = REPO_ROOT / "scripts" / "export_schemas.py"
    r = subprocess.run([sys.executable, str(script)], cwd=tmp_path, check=False)
    assert r.returncode == 0
    assert not (tmp_path / "schemas").exists()
    _assert_schemas_match()
