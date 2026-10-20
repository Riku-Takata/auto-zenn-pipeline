"""Article drafting and validation of the LLM output."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, overload

from pydantic import ValidationError  # type: ignore[import-not-found,unused-ignore]

from auto_zenn_pipeline.adr import dump_adr
from auto_zenn_pipeline.config import Config
from auto_zenn_pipeline.errors import AlreadyGeneratedError, LlmOutputError
from auto_zenn_pipeline.gate import gate_text
from auto_zenn_pipeline.llm import LlmClient, ToolLlmClient
from auto_zenn_pipeline.models import Adr, ArticleDraft, SourceBundle
from auto_zenn_pipeline.session import SessionRef

TITLE_MAX_LEN = 70
EMOJI_MAX_CODEPOINTS = 8
TOPICS_MAX = 5
SLUG_PATTERN = re.compile(r"^[a-z0-9_-]{12,50}$")
SLUG_MAX_LEN = 50

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_SESSION = "bundle"
_STYLE_HEADING = "## style: 書き方の手本"
_STEP = "article"
_TOOL_NAME = "write_article"
_NON_TOPIC_CHARS = re.compile(r"[^a-z0-9]")
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9_-]")
_ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")


def normalize_topics(topics: list[str]) -> list[str]:
    result: list[str] = []
    for t in topics:
        n = _NON_TOPIC_CHARS.sub("", t.lower())
        if not n or n in result:
            continue
        result.append(n)
    return result[:TOPICS_MAX]


def _validate(draft: ArticleDraft, session: str) -> ArticleDraft:
    topics = normalize_topics(draft.topics)
    invalid = (
        draft.title.strip() == ""
        or len(draft.title) > TITLE_MAX_LEN
        or draft.emoji == ""
        or _ASCII_ALNUM.search(draft.emoji) is not None
        or len(draft.emoji) > EMOJI_MAX_CODEPOINTS
        or len(topics) == 0
        or draft.body.strip() == ""
        or draft.body.split("\n", 1)[0].rstrip() == "---"
    )
    if invalid:
        raise LlmOutputError(session=session, step=_STEP)

    return draft.model_copy(update={"topics": topics})


def _build_user(bundle: SourceBundle, guidelines: str) -> str:
    blocks: list[str] = [
        f"# 執筆ガイドライン(writing_guidelines)\n{guidelines}",
        f"# 題材(topic)\n{bundle.topic}",
    ]
    content_items = [item for item in bundle.items if item.kind != "style"]
    style_items = [item for item in bundle.items if item.kind == "style"]

    items_lines = ["# 題材の項目(items)"]
    for item in content_items:
        items_lines.append(f"### {item.id}({item.kind}): {item.title}\n{item.body}")
    blocks.append("\n\n".join(items_lines))

    if style_items:
        style_lines = [_STYLE_HEADING]
        for item in style_items:
            style_lines.append(f"### {item.id}: {item.title}\n{item.body}")
        blocks.append("\n\n".join(style_lines))

    return "\n\n".join(blocks)


@overload
def write_article(
    *,
    llm: LlmClient,
    config: Config,
    bundle: SourceBundle,
    guidelines: str,
) -> ArticleDraft: ...


@overload
def write_article(
    *,
    llm: ToolLlmClient,
    config: Config,
    adr: Adr,
    guidelines: str,
    system_prompt: str,
) -> ArticleDraft: ...


def write_article(
    *,
    llm: Any,
    config: Config,
    guidelines: str,
    bundle: SourceBundle | None = None,
    adr: Adr | None = None,
    system_prompt: str | None = None,
) -> ArticleDraft:
    if bundle is not None:
        system = (_PROMPTS_DIR / "system_generator.md").read_text(encoding="utf-8")
        draft = llm.generate(
            session=_SESSION,
            step=_STEP,
            model=config.models.article_writing,
            system=system,
            user=_build_user(bundle, guidelines),
            output_type=ArticleDraft,
        )
        result = _validate(draft, _SESSION)
        gate_text(result.body, config)
        return result

    if adr is not None and system_prompt is not None:
        return write_article_from_adr(
            llm=llm,
            config=config,
            adr=adr,
            guidelines=guidelines,
            system_prompt=system_prompt,
        )

    raise TypeError("write_article requires either bundle or (adr, system_prompt)")


def write_article_from_adr(
    *,
    llm: ToolLlmClient,
    config: Config,
    adr: Adr,
    guidelines: str,
    system_prompt: str,
) -> ArticleDraft:
    """セッションのログの流れ専用。T-030 で外す。"""
    raw = llm.call_tool(
        session=adr.session,
        step=_STEP,
        model=config.models.article_writing,
        system=system_prompt,
        user=guidelines + "\n\n" + dump_adr(adr),
        tool_name=_TOOL_NAME,
        input_schema=ArticleDraft.model_json_schema(),
        max_tokens=config.limits.max_output_tokens_article,
    )
    try:
        draft = ArticleDraft.model_validate(raw)
    except ValidationError as exc:
        raise LlmOutputError(session=adr.session, step=_STEP) from exc

    return _validate(draft, adr.session)


def fallback_slug(session: SessionRef) -> str:
    sid = _NON_SLUG_CHARS.sub("-", session.session_id.lower())
    return f"log-{session.date.strftime('%Y%m%d')}-{sid}"[:SLUG_MAX_LEN]


def choose_slug(
    draft: ArticleDraft, session: SessionRef, exists: Callable[[str], bool]
) -> str:
    if SLUG_PATTERN.fullmatch(draft.slug) and not exists(draft.slug):
        return draft.slug
    fallback = fallback_slug(session)
    if not exists(fallback):
        return fallback
    raise AlreadyGeneratedError(session=session.name, ref=f"draft/{fallback}")


def render_markdown(draft: ArticleDraft) -> str:
    lines = [
        "---",
        f"title: {json.dumps(draft.title, ensure_ascii=False)}",
        f"emoji: {json.dumps(draft.emoji, ensure_ascii=False)}",
        'type: "tech"',
        f"topics: {json.dumps(draft.topics, ensure_ascii=False)}",
        "published: true",
        "---",
        "",
        draft.body.rstrip("\n"),
    ]
    return "\n".join(lines) + "\n"
