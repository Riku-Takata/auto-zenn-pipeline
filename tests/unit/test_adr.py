import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auto_zenn_pipeline.adr import build_adr, dump_adr, extract_adr
from auto_zenn_pipeline.config import Config, LimitsConfig, ModelsConfig
from auto_zenn_pipeline.errors import LlmOutputError
from auto_zenn_pipeline.models import Adr, AdrExtraction

SESSION = "2026-10-15_abc123"


class _FakeLlm:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def call_tool(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _config() -> Config:
    return Config(
        models=ModelsConfig(
            adr_extraction="claude-sonnet-5-5",
            article_writing="claude-sonnet-5-5",
            evaluation="claude-opus-5-5",
        ),
        limits=LimitsConfig(
            max_log_chars=300000,
            max_output_tokens_adr=8000,
            max_output_tokens_article=16000,
        ),
    )


def _valid_output() -> dict[str, Any]:
    return {
        "worth_writing": True,
        "skip_reason": None,
        "context": "日本語の文脈",
        "options": [
            {
                "name": "A",
                "summary": "案A",
                "proposed_by": "ai",
                "rejected_reason": None,
            }
        ],
        "decision": {"choice": "A", "why": "理由", "tradeoffs": []},
        "ai_steering": [],
    }


def _run(result: dict[str, Any] | Exception) -> AdrExtraction:
    return extract_adr(
        llm=_FakeLlm(result),
        config=_config(),
        session=SESSION,
        rendered="R",
        system_prompt="S",
    )


def _assert_fails(out: dict[str, Any]) -> None:
    with pytest.raises(LlmOutputError) as ei:
        _run(out)
    assert ei.value.step == "adr"
    assert ei.value.session == SESSION


def test_extract_adr_calls_tool_once_with_expected_args() -> None:
    fake = _FakeLlm(_valid_output())
    cfg = _config()
    extract_adr(llm=fake, config=cfg, session=SESSION, rendered="R", system_prompt="S")
    assert fake.calls == [
        {
            "session": SESSION,
            "step": "adr",
            "model": cfg.models.adr_extraction,
            "system": "S",
            "user": "R",
            "tool_name": "record_adr",
            "input_schema": AdrExtraction.model_json_schema(),
            "max_tokens": cfg.limits.max_output_tokens_adr,
        }
    ]


def test_extract_adr_returns_validated_extraction() -> None:
    assert _run(_valid_output()) == AdrExtraction.model_validate(_valid_output())


def test_extract_adr_skip_with_reason() -> None:
    out = _valid_output()
    out.update(worth_writing=False, skip_reason="雑談のみ", context="", options=[])
    out["decision"]["choice"] = ""
    res = _run(out)
    assert res.worth_writing is False
    assert res.skip_reason == "雑談のみ"


@pytest.mark.parametrize("reason", [None, "", "   \n"])
def test_extract_adr_skip_without_reason_raises(reason: str | None) -> None:
    out = _valid_output()
    out.update(worth_writing=False, skip_reason=reason)
    _assert_fails(out)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("context", ""),
        ("context", "  \n\t"),
        ("choice", ""),
        ("choice", "   "),
        ("options", []),
    ],
)
def test_extract_adr_worth_writing_missing_content_raises(
    field: str, value: Any
) -> None:
    out = _valid_output()
    if field == "choice":
        out["decision"]["choice"] = value
    else:
        out[field] = value
    _assert_fails(out)


def test_extract_adr_worth_writing_with_skip_reason_ok() -> None:
    out = _valid_output()
    out["skip_reason"] = "理由"
    assert _run(out).skip_reason == "理由"


def _break_worth(o: dict[str, Any]) -> None:
    del o["worth_writing"]


def _break_decision(o: dict[str, Any]) -> None:
    del o["decision"]


def _break_options(o: dict[str, Any]) -> None:
    o["options"] = "x"


def _break_proposed(o: dict[str, Any]) -> None:
    o["options"][0]["proposed_by"] = "bot"


def _break_bool(o: dict[str, Any]) -> None:
    o["worth_writing"] = "maybe"


@pytest.mark.parametrize(
    "breaker",
    [_break_worth, _break_decision, _break_options, _break_proposed, _break_bool],
)
def test_extract_adr_invalid_shape_raises(breaker: Any) -> None:
    out = _valid_output()
    breaker(out)
    _assert_fails(out)


def test_extract_adr_propagates_llm_output_error() -> None:
    err = LlmOutputError(SESSION, "adr")
    with pytest.raises(LlmOutputError) as ei:
        _run(err)
    assert ei.value is err


def _adr() -> Adr:
    return build_adr(
        AdrExtraction.model_validate(_valid_output()),
        session=SESSION,
        source_commit="0123456789abcdef0123456789abcdef01234567",
        model="claude-sonnet-5-5",
        generated_at=datetime(2026, 10, 15, 12, tzinfo=UTC),
    )


def test_build_adr_attaches_metadata() -> None:
    ext = AdrExtraction.model_validate(_valid_output())
    adr = _adr()
    assert adr.schema_version == 1
    assert adr.session == SESSION
    assert adr.source_commit == "0123456789abcdef0123456789abcdef01234567"
    assert adr.model == "claude-sonnet-5-5"
    assert adr.generated_at == datetime(2026, 10, 15, 12, tzinfo=UTC)
    assert adr.context == ext.context
    assert adr.options == ext.options
    assert adr.decision == ext.decision
    assert adr.ai_steering == ext.ai_steering


def test_build_adr_excludes_extraction_only_fields() -> None:
    dumped = _adr().model_dump()
    assert "worth_writing" not in dumped
    assert "skip_reason" not in dumped


def test_dump_adr_matches_json_dumps() -> None:
    adr = _adr()
    expected = (
        json.dumps(adr.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    )
    assert dump_adr(adr) == expected


def test_dump_adr_roundtrip_non_ascii() -> None:
    adr = _adr()
    s = dump_adr(adr)
    assert Adr.model_validate_json(s) == adr
    assert "日本語の文脈" in s
    assert "\\u" not in s
    assert s.endswith("\n")
    assert not s.endswith("\n\n")


def test_system_prompt_file() -> None:
    path = Path(__file__).resolve().parents[2] / "prompts" / "system_adr_extractor.md"
    text = path.read_text(encoding="utf-8")
    assert text.strip()
    assert re.search(r"[ぁ-ヿ]", text)
    for word in ("Context", "Options", "Decision", "AI Steering", "worth_writing"):
        assert word in text
