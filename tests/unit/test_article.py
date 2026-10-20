import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from auto_zenn_pipeline.adr import build_adr, dump_adr
from auto_zenn_pipeline.article import (
    EMOJI_MAX_CODEPOINTS,
    SLUG_MAX_LEN,
    SLUG_PATTERN,
    TITLE_MAX_LEN,
    TOPICS_MAX,
    normalize_topics,
    write_article,
    write_article_from_adr,
)
from auto_zenn_pipeline.config import Config, LimitsConfig, ModelsConfig
from auto_zenn_pipeline.errors import GateBlockedError, LlmOutputError
from auto_zenn_pipeline.models import (
    Adr,
    AdrExtraction,
    ArticleDraft,
    SourceBundle,
    SourceItem,
)

SESSION = "2026-10-15_abc123"


T = TypeVar("T", bound=BaseModel)


class _FakeLlm:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def call_tool(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _FakeGenLlm:
    def __init__(self, result: BaseModel | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        session: str,
        step: str,
        model: str,
        system: str,
        user: str,
        output_type: type[T],
    ) -> T:
        self.calls.append(
            {
                "session": session,
                "step": step,
                "model": model,
                "system": system,
                "user": user,
                "output_type": output_type,
            }
        )
        if isinstance(self.result, Exception):
            raise self.result
        assert isinstance(self.result, output_type)
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


def _valid_draft() -> ArticleDraft:
    return ArticleDraft(
        title="タイトル",
        emoji="🧭",
        topics=["Python", "GitHub Actions", "python"],
        slug="valid-slug-123",
        body="## はじめに\n本文です。\n",
    )


def _bundle(*items: SourceItem, topic: str = "テスト題材") -> SourceBundle:
    item_list = list(items) if items else [
        SourceItem(
            id="item-1",
            kind="judgment",
            title="判断1",
            body="判断1の本文",
            repo=None,
            confidential=False,
            local_only=False,
        )
    ]
    return SourceBundle(
        topic=topic,
        requested_by="human",
        items=item_list,
        created_at=datetime(2026, 10, 20, 12, tzinfo=UTC),
    )


def _run(result: dict[str, Any] | Exception) -> ArticleDraft:
    return write_article_from_adr(
        llm=_FakeLlm(result),
        config=_config(),
        adr=_adr(),
        guidelines="G",
        system_prompt="S",
    )


def _assert_fails(out: dict[str, Any], adr: Adr | None = None) -> None:
    target_adr = adr if adr is not None else _adr()
    with pytest.raises(LlmOutputError) as ei:
        write_article_from_adr(
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
    assert callable(art.write_article_from_adr)


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
    write_article_from_adr(
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


# --- AC-1 〜 AC-9 のテスト ---


def test_write_article_from_bundle() -> None:
    fake_llm = _FakeGenLlm(_valid_draft())
    cfg = _config()
    item1 = SourceItem(
        id="item-j",
        kind="judgment",
        title="判断タイトル",
        body="判断本文",
        repo=None,
        confidential=False,
        local_only=False,
    )
    item2 = SourceItem(
        id="item-l",
        kind="lesson",
        title="教訓タイトル",
        body="教訓本文",
        repo=None,
        confidential=False,
        local_only=False,
    )
    bundle = _bundle(item1, item2)
    guidelines = "執筆ガイドライン"

    draft = write_article(
        llm=fake_llm,
        config=cfg,
        bundle=bundle,
        guidelines=guidelines,
    )
    assert isinstance(draft, ArticleDraft)
    assert draft.title == "タイトル"
    assert draft.emoji == "🧭"
    assert draft.slug == "valid-slug-123"
    assert draft.body == "## はじめに\n本文です。\n"
    assert draft.topics == ["python", "githubactions"]


def test_generate_arguments() -> None:
    fake_llm = _FakeGenLlm(_valid_draft())
    cfg = _config()
    item1 = SourceItem(
        id="item-j",
        kind="judgment",
        title="判断タイトル",
        body="判断本文",
        repo=None,
        confidential=False,
        local_only=False,
    )
    item2 = SourceItem(
        id="item-l",
        kind="lesson",
        title="教訓タイトル",
        body="教訓本文",
        repo=None,
        confidential=False,
        local_only=False,
    )
    bundle = _bundle(item1, item2)
    guidelines = "執筆ガイドライン"

    write_article(
        llm=fake_llm,
        config=cfg,
        bundle=bundle,
        guidelines=guidelines,
    )
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call["step"] == "article"
    assert call["session"] == "bundle"
    assert call["model"] == cfg.models.article_writing
    assert call["output_type"] is ArticleDraft
    expected_system = (
        Path(__file__).resolve().parents[2] / "prompts" / "system_generator.md"
    ).read_text(encoding="utf-8")
    assert call["system"] == expected_system


def test_user_contains_topic_items_guidelines() -> None:
    fake_llm = _FakeGenLlm(_valid_draft())
    cfg = _config()
    topic = "ユニークな題材トピック-12345"
    body1 = "ユニークな判断本文-abcdef"
    body2 = "ユニークな教訓本文-ghijkl"
    guidelines = "ユニークな執筆ガイドライン-xyz"
    item1 = SourceItem(
        id="item-j",
        kind="judgment",
        title="判断",
        body=body1,
        repo=None,
        confidential=False,
        local_only=False,
    )
    item2 = SourceItem(
        id="item-l",
        kind="lesson",
        title="教訓",
        body=body2,
        repo=None,
        confidential=False,
        local_only=False,
    )
    bundle = _bundle(item1, item2, topic=topic)

    write_article(
        llm=fake_llm,
        config=cfg,
        bundle=bundle,
        guidelines=guidelines,
    )
    user = fake_llm.calls[0]["user"]
    assert topic in user
    assert body1 in user
    assert body2 in user
    assert guidelines in user


def test_style_items_after_heading() -> None:
    fake_llm = _FakeGenLlm(_valid_draft())
    cfg = _config()
    judgment_body = "判断の本文-前側"
    style_body = "手本の本文-後ろ側"
    item_j = SourceItem(
        id="item-j",
        kind="judgment",
        title="判断",
        body=judgment_body,
        repo=None,
        confidential=False,
        local_only=False,
    )
    item_s = SourceItem(
        id="item-s",
        kind="style",
        title="手本",
        body=style_body,
        repo=None,
        confidential=False,
        local_only=False,
    )
    bundle = _bundle(item_j, item_s)

    write_article(
        llm=fake_llm,
        config=cfg,
        bundle=bundle,
        guidelines="ガイドライン",
    )
    user = fake_llm.calls[0]["user"]
    heading = "## style: 書き方の手本"
    assert user.splitlines().count(heading) == 1
    idx_heading = user.index(heading)
    idx_judgment = user.index(judgment_body)
    idx_style = user.index(style_body)
    assert idx_judgment < idx_heading < idx_style


def test_no_style_heading_without_style_items() -> None:
    fake_llm = _FakeGenLlm(_valid_draft())
    cfg = _config()
    item_j = SourceItem(
        id="item-j",
        kind="judgment",
        title="判断",
        body="判断本文",
        repo=None,
        confidential=False,
        local_only=False,
    )
    bundle = _bundle(item_j)
    real_guidelines = (
        Path(__file__).resolve().parents[2] / "prompts" / "writing_guidelines.md"
    ).read_text(encoding="utf-8")

    write_article(
        llm=fake_llm,
        config=cfg,
        bundle=bundle,
        guidelines=real_guidelines,
    )
    user = fake_llm.calls[0]["user"]
    heading = "## style: 書き方の手本"
    assert heading not in user.splitlines()


def test_email_in_body_blocks() -> None:
    bad_draft = ArticleDraft(
        title="タイトル",
        emoji="🧭",
        topics=["Python", "GitHub Actions", "python"],
        slug="valid-slug-123",
        body="## はじめに\n連絡先は taro@example.com です。\n",
    )
    fake_llm = _FakeGenLlm(bad_draft)
    cfg = _config()
    bundle = _bundle()

    with pytest.raises(GateBlockedError) as exc_info:
        write_article(
            llm=fake_llm,
            config=cfg,
            bundle=bundle,
            guidelines="ガイドライン",
        )
    assert exc_info.value.target == "text"
    assert "personal_info" in exc_info.value.reasons


@pytest.mark.parametrize(
    "patch",
    [
        {"topics": ["", "!!"]},
        {"title": "   "},
        {"title": "あ" * 71},
        {"emoji": ""},
        {"emoji": "a🧭"},
        {"emoji": "🧭" * 9},
        {"body": " \n\t\n"},
        {"body": "---\ntitle: x\n---\n本文"},
    ],
)
def test_new_write_article_validation_failures(patch: dict[str, Any]) -> None:
    base = _valid_draft().model_dump()
    base.update(patch)
    invalid_draft = ArticleDraft.model_validate(base)
    fake_llm = _FakeGenLlm(invalid_draft)
    cfg = _config()
    bundle = _bundle()

    with pytest.raises(LlmOutputError) as exc_info:
        write_article(
            llm=fake_llm,
            config=cfg,
            bundle=bundle,
            guidelines="ガイドライン",
        )
    assert exc_info.value.step == "article"
    assert exc_info.value.session == "bundle"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "あ" * 70),
        ("emoji", "👨\u200d💻"),
    ],
)
def test_new_write_article_validation_successes(field: str, value: Any) -> None:
    base = _valid_draft().model_dump()
    base[field] = value
    valid_draft = ArticleDraft.model_validate(base)
    fake_llm = _FakeGenLlm(valid_draft)
    cfg = _config()
    bundle = _bundle()

    result = write_article(
        llm=fake_llm,
        config=cfg,
        bundle=bundle,
        guidelines="ガイドライン",
    )
    assert getattr(result, field) == value


def test_generate_error_propagates() -> None:
    fake_llm = _FakeGenLlm(LlmOutputError(session="bundle", step="article"))
    cfg = _config()
    bundle = _bundle()

    with pytest.raises(LlmOutputError) as exc_info:
        write_article(
            llm=fake_llm,
            config=cfg,
            bundle=bundle,
            guidelines="ガイドライン",
        )
    assert exc_info.value.step == "article"
    assert exc_info.value.session == "bundle"


def test_system_generator_prompt_content() -> None:
    path = Path(__file__).resolve().parents[2] / "prompts" / "system_generator.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    required = [
        "日本語",
        "topic",
        "items にないこと",
        "style",
        "writing_guidelines",
        "個人的なこと",
        "未公開",
        "frontmatter",
        "H1",
        "12〜50",
    ]
    for req in required:
        assert req in text
    assert "ADR" not in text
