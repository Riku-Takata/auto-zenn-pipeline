import datetime
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from auto_zenn_pipeline import article
from auto_zenn_pipeline.article import (
    choose_slug,
    fallback_slug,
    render_markdown,
)
from auto_zenn_pipeline.errors import AlreadyGeneratedError
from auto_zenn_pipeline.models import ArticleDraft
from auto_zenn_pipeline.session import SessionRef


def _session(
    *,
    name: str = "2026-10-15_valid-id",
    date: datetime.date = datetime.date(2026, 10, 15),
    session_id: str = "valid-id",
) -> SessionRef:
    return SessionRef(
        repo_root=Path("/nonexistent/repo"),
        name=name,
        path=Path(f"/nonexistent/repo/raw_logs/{name}"),
        date=date,
        session_id=session_id,
    )


def _draft(
    *,
    title: str = "T",
    emoji: str = "🧭",
    topics: list[str] | None = None,
    slug: str = "valid-slug-123",
    body: str = "本文",
) -> ArticleDraft:
    return ArticleDraft(
        title=title,
        emoji=emoji,
        topics=["python", "githubactions"] if topics is None else topics,
        slug=slug,
        body=body,
    )


def _parse_frontmatter(out: str) -> dict[str, Any]:
    lines = out.split("\n")
    assert lines[0] == "---"
    second_idx = lines.index("---", 1)
    fm_text = "\n".join(lines[1:second_idx])

    if yaml is not None:
        data: Any = yaml.safe_load(fm_text)
        assert isinstance(data, dict)
        return data

    result: dict[str, Any] = {}
    for line in lines[1:second_idx]:
        if not line:
            continue
        key, val = line.split(": ", 1)
        if key == "published":
            result[key] = val == "true"
        elif key == "type":
            result[key] = json.loads(val) if val.startswith('"') else val
        elif key in ("title", "emoji", "topics"):
            result[key] = json.loads(val)
        else:
            result[key] = val
    return result


def test_public_api_and_constants() -> None:
    assert callable(article.fallback_slug)
    assert callable(article.choose_slug)
    assert callable(article.render_markdown)
    assert article.SLUG_PATTERN.pattern == r"^[a-z0-9_-]{12,50}$"
    assert article.SLUG_MAX_LEN == 50


def test_fallback_slug_basic() -> None:
    session = _session(
        name="2026-10-15_AbC_x-1",
        date=datetime.date(2026, 10, 15),
        session_id="AbC_x-1",
    )
    assert fallback_slug(session) == "log-20261015-abc_x-1"


def test_fallback_slug_truncated() -> None:
    session = _session(
        name="2026-10-15_" + "a" * 100,
        date=datetime.date(2026, 10, 15),
        session_id="a" * 100,
    )
    result = fallback_slug(session)
    assert result == "log-20261015-" + "a" * 37
    assert len(result) == 50


def test_fallback_slug_replaces_chars() -> None:
    session = _session(
        name="2026-01-02_custom",
        date=datetime.date(2026, 1, 2),
        session_id="A.b c/D",
    )
    assert fallback_slug(session) == "log-20260102-a-b-c-d"


def test_fallback_slug_pure() -> None:
    session = _session(
        name="2026-10-15_AbC_x-1",
        date=datetime.date(2026, 10, 15),
        session_id="AbC_x-1",
    )
    val1 = fallback_slug(session)
    val2 = fallback_slug(session)
    assert val1 == val2 == "log-20261015-abc_x-1"


def test_choose_slug_valid() -> None:
    draft = _draft(slug="valid-slug-123")
    session = _session()
    assert choose_slug(draft, session, lambda _: False) == "valid-slug-123"


@pytest.mark.parametrize(
    "invalid_slug",
    [
        "a" * 11,
        "a" * 51,
        "Valid-slug-123",
        "valid.slug-123",
    ],
)
def test_choose_slug_invalid_falls_back(invalid_slug: str) -> None:
    draft = _draft(slug=invalid_slug)
    session = _session()
    assert choose_slug(draft, session, lambda _: False) == fallback_slug(session)


def test_choose_slug_collision_falls_back() -> None:
    draft = _draft(slug="valid-slug-123")
    session = _session()
    assert (
        choose_slug(draft, session, lambda s: s == "valid-slug-123")
        == fallback_slug(session)
    )


def test_choose_slug_both_collide() -> None:
    draft = _draft(slug="valid-slug-123")
    session = _session()
    expected_fallback = fallback_slug(session)

    with pytest.raises(AlreadyGeneratedError) as exc_info:
        choose_slug(
            draft,
            session,
            lambda s: s in ("valid-slug-123", expected_fallback),
        )
    assert exc_info.value.session == session.name
    assert exc_info.value.ref == f"draft/{expected_fallback}"


def test_choose_slug_invalid_and_fallback_collides() -> None:
    draft = _draft(slug="short")
    session = _session()
    expected_fallback = fallback_slug(session)

    with pytest.raises(AlreadyGeneratedError) as exc_info:
        choose_slug(
            draft,
            session,
            lambda s: s == expected_fallback,
        )
    assert exc_info.value.session == session.name
    assert exc_info.value.ref == f"draft/{expected_fallback}"


def test_render_returns_str() -> None:
    draft = _draft()
    out = render_markdown(draft)
    assert isinstance(out, str)

    sig = inspect.signature(render_markdown)
    assert len(sig.parameters) == 1


def test_render_exact() -> None:
    draft = ArticleDraft(
        title="T",
        emoji="🧭",
        topics=["python", "githubactions"],
        slug="valid-slug-123",
        body="本文",
    )
    expected = (
        '---\ntitle: "T"\nemoji: "🧭"\ntype: "tech"\ntopics: '
        '["python", "githubactions"]\npublished: true\n---\n\n本文\n'
    )
    assert render_markdown(draft) == expected


def test_render_frontmatter_yaml() -> None:
    draft = _draft(
        title="T",
        emoji="🧭",
        topics=["python", "githubactions"],
    )
    out = render_markdown(draft)
    data = _parse_frontmatter(out)

    assert isinstance(data, dict)
    assert list(data.keys()) == ["title", "emoji", "type", "topics", "published"]
    assert data["title"] == draft.title
    assert data["emoji"] == draft.emoji
    assert data["type"] == "tech"
    assert data["topics"] == draft.topics
    assert data["published"] is True


def test_render_title_roundtrip() -> None:
    title = 'a "q": #x\n2行目'
    draft = _draft(title=title)
    out = render_markdown(draft)
    data = _parse_frontmatter(out)

    assert isinstance(data, dict)
    assert data["title"] == title


@pytest.mark.parametrize("body", ["本文\n\n\n", "本文"])
def test_render_body_trailing_newlines(body: str) -> None:
    draft = _draft(body=body)
    out = render_markdown(draft)

    lines = out.split("\n")
    second_idx = lines.index("---", 1)
    assert lines[second_idx + 1] == ""
    assert lines[second_idx + 2] == "本文"

    assert out.endswith("---\n\n本文\n")
    assert not out.endswith("\n\n")


def test_render_omits_slug() -> None:
    draft = _draft(slug="unique-slug-xyz-999")
    out = render_markdown(draft)
    assert "unique-slug-xyz-999" not in out


def test_article_does_not_import_yaml() -> None:
    src = inspect.getsource(article)
    assert "import yaml" not in src
    assert "from yaml" not in src
