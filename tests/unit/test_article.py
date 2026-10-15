import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auto_zenn_pipeline.adr import build_adr, dump_adr
from auto_zenn_pipeline.article import (
    EMOJI_MAX_CODEPOINTS,
    SLUG_MAX_LEN,
    SLUG_PATTERN,
    TITLE_MAX_LEN,
    TOPICS_MAX,
    normalize_topics,
    write_article,
)
from auto_zenn_pipeline.config import Config, LimitsConfig, ModelsConfig
from auto_zenn_pipeline.errors import LlmOutputError
from auto_zenn_pipeline.models import Adr, AdrExtraction, ArticleDraft

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


def _adr() -> Adr:
    extraction = AdrExtraction(
        worth_writing=True,
        skip_reason=None,
        context="テストの文脈",
        options=[
            {
                "name": "案A",
                "summary": "案Aの概要",
                "proposed_by": "human",
                "rejected_reason": None,
            }
        ],
        decision={"choice": "案A", "why": "理由", "tradeoffs": []},
        ai_steering=[],
    )
    return build_adr(
        extraction,
        session=SESSION,
        source_commit="0123456789abcdef0123456789abcdef01234567",
        model="claude-sonnet-5-5",
        generated_at=datetime(2026, 10, 15, 12, tzinfo=UTC),
    )


def _valid_output() -> dict[str, Any]:
    return {
        "title": " タイトル ",
        "emoji": "🧭",
        "topics": ["Python", "GitHub Actions"],
        "slug": "valid-slug-123",
        "body": "## はじめに\n本文です。\n",
    }


def _run(result: dict[str, Any] | Exception) -> ArticleDraft:
    return write_article(
        llm=_FakeLlm(result),
        config=_config(),
        adr=_adr(),
        guidelines="G",
        system_prompt="S",
    )


def _assert_fails(out: dict[str, Any], adr: Adr | None = None) -> None:
    target_adr = adr if adr is not None else _adr()
    with pytest.raises(LlmOutputError) as ei:
        write_article(
            llm=_FakeLlm(out),
            config=_config(),
            adr=target_adr,
            guidelines="G",
            system_prompt="S",
        )
    assert ei.value.step == "article"
    assert ei.value.session == target_adr.session


def test_public_names_importable() -> None:
    import auto_zenn_pipeline.article as art

    assert art.TITLE_MAX_LEN is not None
    assert art.EMOJI_MAX_CODEPOINTS is not None
    assert art.TOPICS_MAX is not None
    assert art.SLUG_PATTERN is not None
    assert art.SLUG_MAX_LEN is not None
    assert callable(art.normalize_topics)
    assert callable(art.write_article)


def test_constants() -> None:
    assert TITLE_MAX_LEN == 70
    assert EMOJI_MAX_CODEPOINTS == 8
    assert TOPICS_MAX == 5
    assert SLUG_MAX_LEN == 50
    assert isinstance(SLUG_PATTERN, re.Pattern)
    assert SLUG_PATTERN.pattern == r"^[a-z0-9_-]{12,50}$"


def test_normalize_topics_example() -> None:
    input_topics = ["Python", "GitHub Actions", "python", "", "!!", "a", "b", "c", "d"]
    expected = ["python", "githubactions", "a", "b", "c"]
    assert normalize_topics(input_topics) == expected


def test_normalize_topics_does_not_mutate() -> None:
    input_topics = ["Python", "GitHub Actions", "python", "", "!!", "a", "b", "c", "d"]
    original = list(input_topics)
    _ = normalize_topics(input_topics)
    assert input_topics == original


@pytest.mark.parametrize(
    "topics",
    [
        [],
        ["", "!!", "ー"],
    ],
)
def test_normalize_topics_empty(topics: list[str]) -> None:
    assert normalize_topics(topics) == []


def test_call_tool_arguments() -> None:
    fake = _FakeLlm(_valid_output())
    cfg = _config()
    adr = _adr()
    guidelines = "テストガイドライン"
    system_prompt = "テストシステムプロンプト"
    write_article(
        llm=fake,
        config=cfg,
        adr=adr,
        guidelines=guidelines,
        system_prompt=system_prompt,
    )
    assert len(fake.calls) == 1
    assert fake.calls[0] == {
        "session": adr.session,
        "step": "article",
        "model": cfg.models.article_writing,
        "system": system_prompt,
        "user": guidelines + "\n\n" + dump_adr(adr),
        "tool_name": "write_article",
        "input_schema": ArticleDraft.model_json_schema(),
        "max_tokens": cfg.limits.max_output_tokens_article,
    }


def test_only_topics_normalized() -> None:
    raw = {
        "title": " タイトル ",
        "emoji": "🧭",
        "topics": ["Python", "GitHub Actions", "python"],
        "slug": "INVALID Slug!",
        "body": "本文\n",
    }
    result = _run(raw)
    assert isinstance(result, ArticleDraft)
    assert result.title == " タイトル "
    assert result.emoji == "🧭"
    assert result.topics == ["python", "githubactions"]
    assert result.slug == "INVALID Slug!"
    assert result.body == "本文\n"


@pytest.mark.parametrize(
    "missing_field",
    ["title", "emoji", "topics", "slug", "body"],
)
def test_validation_error_missing_field(missing_field: str) -> None:
    out = _valid_output()
    del out[missing_field]
    _assert_fails(out)


def test_validation_error_topics_not_list() -> None:
    out = _valid_output()
    out["topics"] = "python"
    _assert_fails(out)


@pytest.mark.parametrize(
    "topics",
    [
        ["", "!!"],
        [],
    ],
)
def test_empty_topics(topics: list[str]) -> None:
    out = _valid_output()
    out["topics"] = topics
    _assert_fails(out)


@pytest.mark.parametrize(
    "title",
    [
        "   ",
        "",
        "あ" * 71,
    ],
)
def test_invalid_title(title: str) -> None:
    out = _valid_output()
    out["title"] = title
    _assert_fails(out)


def test_title_70_ok() -> None:
    out = _valid_output()
    out["title"] = "あ" * 70
    res = _run(out)
    assert res.title == "あ" * 70


@pytest.mark.parametrize(
    "emoji",
    [
        "",
        "a🧭",
        "1🧭",
        "🧭" * 9,
    ],
)
def test_invalid_emoji(emoji: str) -> None:
    out = _valid_output()
    out["emoji"] = emoji
    _assert_fails(out)


@pytest.mark.parametrize(
    "emoji",
    [
        "👨\u200d💻",
        "🧭" * 8,
    ],
)
def test_valid_emoji(emoji: str) -> None:
    out = _valid_output()
    out["emoji"] = emoji
    res = _run(out)
    assert res.emoji == emoji


@pytest.mark.parametrize(
    "body",
    [
        "",
        " \n\t\n",
    ],
)
def test_blank_body(body: str) -> None:
    out = _valid_output()
    out["body"] = body
    _assert_fails(out)


@pytest.mark.parametrize(
    "body",
    [
        "---\ntitle: x\n---\n本文",
        "---  \n本文",
        "---\r\n本文",
        "---",
    ],
)
def test_frontmatter_body_rejected(body: str) -> None:
    out = _valid_output()
    out["body"] = body
    _assert_fails(out)


@pytest.mark.parametrize(
    "body",
    [
        "----",
        " ---",
        "本文",
        "本文\n---\n続き",
    ],
)
def test_body_first_line_ok(body: str) -> None:
    out = _valid_output()
    out["body"] = body
    res = _run(out)
    assert res.body == body


def test_system_generator_prompt_exists() -> None:
    path = Path(__file__).resolve().parents[2] / "prompts" / "system_generator.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.strip() != ""
