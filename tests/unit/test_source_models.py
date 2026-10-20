import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from auto_zenn_pipeline.models import (
    Candidate,
    CandidateList,
    SourceBundle,
    SourceItem,
    export_json_schemas,
)

_MODELS = (SourceItem, SourceBundle, Candidate, CandidateList)


def _bundle() -> dict[str, Any]:
    return {
        "topic": "束から記事を書く設計",
        "requested_by": "human",
        "items": [
            {
                "id": "J-001",
                "kind": "judgment",
                "title": "t",
                "body": "b",
                "repo": "Riku-Takata/auto-zenn-pipeline",
                "confidential": False,
                "local_only": False,
            },
            {
                "id": "L-002",
                "kind": "lesson",
                "title": "t2",
                "body": "b2",
                "repo": None,
                "confidential": True,
                "local_only": True,
            },
        ],
        "created_at": "2026-10-20T09:00:00+09:00",
    }


def _candidates() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "title": "t",
                "item_ids": ["J-001", "L-002"],
                "value": "high",
                "reason": "r",
            }
        ]
    }


def test_bundle_reads() -> None:
    b = SourceBundle.model_validate_json(json.dumps(_bundle(), ensure_ascii=False))
    assert b.topic == "束から記事を書く設計"
    assert b.requested_by == "human"
    assert len(b.items) == 2
    assert b.items[0].id == "J-001"
    assert b.items[0].kind == "judgment"
    assert b.items[0].repo == "Riku-Takata/auto-zenn-pipeline"
    assert b.items[0].confidential is False
    assert b.items[0].local_only is False
    assert b.items[1].repo is None
    assert b.items[1].confidential is True
    assert b.items[1].local_only is True
    assert isinstance(b.created_at, datetime)
    assert b.created_at.tzinfo is not None


def test_requested_by_candidate() -> None:
    d = _bundle()
    d["requested_by"] = "candidate"
    b = SourceBundle.model_validate_json(json.dumps(d, ensure_ascii=False))
    assert b.requested_by == "candidate"


@pytest.mark.parametrize(
    "kind",
    [
        "judgment",
        "lesson",
        "principle",
        "style",
        "handoff",
        "voice",
        "conversation",
    ],
)
def test_kind_accepted(kind: str) -> None:
    d = _bundle()
    items = cast(list[dict[str, Any]], d["items"])
    items[0]["kind"] = kind
    b = SourceBundle.model_validate_json(json.dumps(d, ensure_ascii=False))
    assert b.items[0].kind == kind


def _empty_items(d: dict[str, Any]) -> None:
    d["items"] = []


def _empty_topic(d: dict[str, Any]) -> None:
    d["topic"] = ""


def _invalid_kind(d: dict[str, Any]) -> None:
    cast(list[dict[str, Any]], d["items"])[0]["kind"] = "memo"


def _invalid_requested_by(d: dict[str, Any]) -> None:
    d["requested_by"] = "ai"


def _naive_created_at(d: dict[str, Any]) -> None:
    d["created_at"] = "2026-10-20T09:00:00"


@pytest.mark.parametrize(
    "mutator",
    [
        _empty_items,
        _empty_topic,
        _invalid_kind,
        _invalid_requested_by,
        _naive_created_at,
    ],
)
def test_rejects(mutator: Callable[[dict[str, Any]], None]) -> None:
    d = _bundle()
    mutator(d)
    with pytest.raises(ValidationError):
        SourceBundle.model_validate_json(json.dumps(d, ensure_ascii=False))


@pytest.mark.parametrize("field", ["confidential", "local_only"])
def test_flag_required(field: str) -> None:
    d = _bundle()
    items = cast(list[dict[str, Any]], d["items"])
    del items[0][field]
    with pytest.raises(ValidationError):
        SourceBundle.model_validate_json(json.dumps(d, ensure_ascii=False))


def test_candidates_read() -> None:
    cl = CandidateList.model_validate_json(
        '{"candidates":[{"title":"t","item_ids":["J-001","L-002"],"value":"high","reason":"r"}]}'
    )
    assert cl.candidates[0].item_ids == ["J-001", "L-002"]
    assert cl.candidates[0].value == "high"


@pytest.mark.parametrize("value", ["high", "medium", "low"])
def test_candidate_value_accepted(value: str) -> None:
    d = _candidates()
    candidates = cast(list[dict[str, Any]], d["candidates"])
    candidates[0]["value"] = value
    cl = CandidateList.model_validate_json(json.dumps(d, ensure_ascii=False))
    assert cl.candidates[0].value == value


def _invalid_value(d: dict[str, Any]) -> None:
    cast(list[dict[str, Any]], d["candidates"])[0]["value"] = "none"


def _empty_item_ids(d: dict[str, Any]) -> None:
    cast(list[dict[str, Any]], d["candidates"])[0]["item_ids"] = []


@pytest.mark.parametrize("mutator", [_invalid_value, _empty_item_ids])
def test_candidate_rejects(mutator: Callable[[dict[str, Any]], None]) -> None:
    d = _candidates()
    mutator(d)
    with pytest.raises(ValidationError):
        CandidateList.model_validate_json(json.dumps(d, ensure_ascii=False))


def test_candidates_empty() -> None:
    cl = CandidateList.model_validate_json('{"candidates":[]}')
    assert cl.candidates == []


def test_export_keys_include_new() -> None:
    assert set(export_json_schemas()) == {
        "chat_history.schema.json",
        "git_context.schema.json",
        "adr.schema.json",
        "source_bundle.schema.json",
        "candidates.schema.json",
    }


def test_export_values_new() -> None:
    out = export_json_schemas()
    for name, m in (
        ("source_bundle.schema.json", SourceBundle),
        ("candidates.schema.json", CandidateList),
    ):
        expected = (
            json.dumps(m.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        )
        assert out[name] == expected


def test_new_schema_files_match() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    schemas = repo_root / "schemas"
    expected = export_json_schemas()
    for name in ("source_bundle.schema.json", "candidates.schema.json"):
        path = schemas / name
        assert path.exists()
        assert path.read_text(encoding="utf-8") == expected[name]
