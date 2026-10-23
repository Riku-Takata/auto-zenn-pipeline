"""Publication gate for source bundles and written articles."""

import re
from dataclasses import dataclass
from typing import Literal

from auto_zenn_pipeline.config import Config
from auto_zenn_pipeline.errors import GateBlockedError
from auto_zenn_pipeline.models import SourceBundle, SourceItem
from auto_zenn_pipeline.secret_scan import PATTERNS

GateReason = Literal[
    "confidential",
    "local_only",
    "exclude_repo",
    "deny_term",
    "secret",
    "personal_info",
]

_ORDER: tuple[GateReason, ...] = (
    "confidential",
    "local_only",
    "exclude_repo",
    "deny_term",
    "secret",
    "personal_info",
)

_PERSONAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    re.compile(
        r"(?<![0-9A-Za-z+-])(?:0\d{1,4}|\+81-?\d{1,4})-\d{1,4}-\d{3,4}(?![0-9A-Za-z-])"
    ),
    re.compile(r"/(?:Users|home)/[^/\s]+/"),
)


@dataclass(frozen=True)
class GateRemoval:
    id: str
    reason: GateReason


@dataclass(frozen=True)
class GateResult:
    bundle: SourceBundle
    removed: list[GateRemoval]


def _text_reasons(text: str, config: Config) -> list[GateReason]:
    folded = text.casefold()
    reasons: list[GateReason] = []
    if any(r.casefold() in folded for r in config.privacy.exclude_repos):
        reasons.append("exclude_repo")
    if any(t.casefold() in folded for t in config.privacy.deny_terms):
        reasons.append("deny_term")
    if any(p.regex.search(text) is not None for p in PATTERNS):
        reasons.append("secret")
    if any(p.search(text) is not None for p in _PERSONAL_PATTERNS):
        reasons.append("personal_info")
    return reasons


def _item_reason(item: SourceItem, config: Config) -> GateReason | None:
    hit_reasons: set[GateReason] = set()
    if item.confidential:
        hit_reasons.add("confidential")
    if item.local_only:
        hit_reasons.add("local_only")
    if item.repo is not None and any(
        item.repo.casefold() == r.casefold() for r in config.privacy.exclude_repos
    ):
        hit_reasons.add("exclude_repo")
    hit_reasons.update(_text_reasons(item.title, config))
    hit_reasons.update(_text_reasons(item.body, config))

    for r in _ORDER:
        if r in hit_reasons:
            return r
    return None


def gate_bundle(bundle: SourceBundle, config: Config) -> GateResult:
    kept: list[SourceItem] = []
    removed: list[GateRemoval] = []
    for item in bundle.items:
        reason = _item_reason(item, config)
        if reason is not None:
            removed.append(GateRemoval(id=item.id, reason=reason))
        else:
            kept.append(item)

    if not kept:
        removed_reasons = {r.reason for r in removed}
        reasons = tuple(r for r in _ORDER if r in removed_reasons)
        raise GateBlockedError("bundle", reasons)

    return GateResult(
        bundle=bundle.model_copy(update={"items": kept}),
        removed=removed,
    )


def gate_text(text: str, config: Config) -> None:
    reasons = _text_reasons(text, config)
    if reasons:
        raise GateBlockedError("text", tuple(reasons))
