"""List article candidates from a gated source bundle."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from auto_zenn_pipeline.config import Config
from auto_zenn_pipeline.errors import SourcesError
from auto_zenn_pipeline.llm import LlmClient
from auto_zenn_pipeline.models import CandidateList, SourceBundle

__all__ = [
    "WrittenArticle",
    "filter_candidates",
    "list_candidates",
    "load_written_articles",
    "render_candidates_markdown",
]

_SESSION = "bundle"
_STEP = "candidates"
_VALUE_LABELS = {"high": "高", "medium": "中", "low": "低"}


@dataclass(frozen=True)
class WrittenArticle:
    topic: str
    item_ids: frozenset[str]


def load_written_articles(sources_dir: Path) -> list[WrittenArticle]:
    if not sources_dir.is_dir():
        return []
    articles: list[WrittenArticle] = []
    for path in sorted(sources_dir.glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise SourcesError(str(path), "unreadable") from None
        try:
            data = json.loads(text)
        except ValueError:
            raise SourcesError(str(path), "invalid") from None
        if not isinstance(data, dict):
            raise SourcesError(str(path), "invalid")
        ids = data.get("item_ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise SourcesError(str(path), "invalid")
        topic = data.get("topic")
        articles.append(
            WrittenArticle(
                topic=topic if isinstance(topic, str) else "",
                item_ids=frozenset(ids),
            )
        )
    return articles


def _build_user(bundle: SourceBundle, written: Sequence[WrittenArticle]) -> str:
    blocks = ["# 項目(items)"]
    for item in bundle.items:
        blocks.append(f"### {item.id}({item.kind}): {item.title}\n{item.body}")
    blocks.append("# 書いた記事(written)")
    if written:
        blocks.append(
            "\n".join(f"- {w.topic}: {', '.join(sorted(w.item_ids))}" for w in written)
        )
    else:
        blocks.append("(なし)")
    return "\n\n".join(blocks)


def filter_candidates(
    result: CandidateList,
    allowed_ids: set[str],
    written: Sequence[WrittenArticle],
) -> CandidateList:
    kept = []
    for c in result.candidates:
        ids = set(c.item_ids)
        if ids - allowed_ids:
            continue
        if any(ids <= w.item_ids for w in written):
            continue
        kept.append(c)
    return CandidateList(candidates=kept)


def list_candidates(
    *,
    llm: LlmClient,
    config: Config,
    bundle: SourceBundle,
    written: Sequence[WrittenArticle],
    system: str,
) -> CandidateList:
    result = llm.generate(
        session=_SESSION,
        step=_STEP,
        model=config.models.article_writing,
        system=system,
        user=_build_user(bundle, written),
        output_type=CandidateList,
    )
    return filter_candidates(result, {i.id for i in bundle.items}, written)


def render_candidates_markdown(result: CandidateList) -> str:
    out = "# 記事の候補\n"
    if not result.candidates:
        return out + "\n候補はありません。\n"
    for n, c in enumerate(result.candidates, start=1):
        out += (
            f"\n## {n}. {c.title}\n\n"
            f"- 価値: {_VALUE_LABELS[c.value]}\n"
            f"- 理由: {c.reason}\n"
            f"- 項目: {len(c.item_ids)} 件\n"
        )
    return out
