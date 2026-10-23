"""Tests for azp candidates (candidates.py and the CLI subcommand)."""

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar, cast

import pytest

from auto_zenn_pipeline.cli import main
from auto_zenn_pipeline.llm import LlmClient
from auto_zenn_pipeline.models import (
    Candidate,
    CandidateList,
    SourceBundle,
    SourceItem,
)

T = TypeVar("T")


class FakeLlm(LlmClient):
    def __init__(self, result: CandidateList | None = None) -> None:
        self.result = result if result is not None else CandidateList(candidates=[])
        self.call_count = 0
        self.calls: list[dict[str, object]] = []

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
        self.call_count += 1
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
        return cast(T, self.result)


def _setup_repo(tmp_path: Path, deny_terms: str = "") -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    toml = Path("pipeline.toml").read_text(encoding="utf-8")
    if deny_terms:
        toml = toml.replace("deny_terms = []", f'deny_terms = ["{deny_terms}"]')
    (repo_root / "pipeline.toml").write_text(toml, encoding="utf-8")
    prompts = repo_root / "prompts"
    prompts.mkdir()
    for f in Path("prompts").glob("*.md"):
        (prompts / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    return repo_root


def _item(
    item_id: str,
    *,
    body: str | None = None,
    confidential: bool = False,
    local_only: bool = False,
) -> SourceItem:
    return SourceItem(
        id=item_id,
        kind="judgment",
        title=f"TITLE_{item_id}",
        body=body if body is not None else f"BODY_{item_id}",
        repo=None,
        confidential=confidential,
        local_only=local_only,
    )


def _write_bundle(tmp_path: Path, items: list[SourceItem]) -> Path:
    bundle = SourceBundle(
        topic="束の題材",
        requested_by="human",
        items=items,
        created_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    path = tmp_path / "in" / "bundle.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.model_dump_json(), encoding="utf-8")
    return path


def _write_source(
    sources: Path, slug: str, topic: str, item_ids: list[str]
) -> None:
    sources.mkdir(parents=True, exist_ok=True)
    (sources / f"{slug}.json").write_text(
        json.dumps(
            {
                "slug": slug,
                "topic": topic,
                "item_ids": item_ids,
                "created_at": "2026-10-20T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def _cand(
    title: str,
    ids: list[str],
    value: Literal["high", "medium", "low"] = "high",
    reason: str = "理由",
) -> Candidate:
    return Candidate(title=title, item_ids=ids, value=value, reason=reason)


def _run(
    tmp_path: Path,
    bundle: Path,
    llm: FakeLlm,
    sources: Path | None = None,
    repo_root: Path | None = None,
) -> int:
    root = repo_root if repo_root is not None else _setup_repo(tmp_path)
    argv = ["candidates", str(bundle)]
    if sources is not None:
        argv += ["--sources-dir", str(sources)]
    return main(argv, llm=llm, repo_root=root)


def test_ac1_missing_bundle_arg() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["candidates"])
    assert exc.value.code == 2


def test_ac2_ac3_ac4_outputs_and_llm_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _write_bundle(tmp_path, [_item("A"), _item("B"), _item("C")])
    (bundle.parent / "candidates.json").write_text("old", encoding="utf-8")
    cands = CandidateList(
        candidates=[_cand("第二", ["B"], "low"), _cand("第一", ["A", "C"], "medium")]
    )
    llm = FakeLlm(cands)
    sources = tmp_path / "empty"
    sources.mkdir()
    assert _run(tmp_path, bundle, llm, sources) == 0
    loaded = CandidateList.model_validate_json(
        (bundle.parent / "candidates.json").read_text(encoding="utf-8")
    )
    assert loaded == cands
    assert (bundle.parent / "candidates.md").exists()
    out = capsys.readouterr().out
    assert "candidates.json" in out and "candidates.md" in out

    assert llm.call_count == 1
    call = llm.calls[0]
    assert call["output_type"] is CandidateList
    config = tomllib.loads(Path("pipeline.toml").read_text(encoding="utf-8"))
    assert call["model"] == config["models"]["article_writing"]
    prompt = Path("prompts/system_candidates.md").read_text(encoding="utf-8")
    assert prompt in str(call["system"])


def test_ac5_user_has_items(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, [_item("A"), _item("B")])
    llm = FakeLlm()
    assert _run(tmp_path, bundle, llm, tmp_path / "none") == 0
    user = str(llm.calls[0]["user"])
    for i in ("A", "B"):
        assert i in user and f"TITLE_{i}" in user and f"BODY_{i}" in user


def test_ac6_excluded_items_not_sent(tmp_path: Path) -> None:
    secret = "sk-ant-" + "a" * 30
    items = [
        _item("OK"),
        _item("CONF", confidential=True),
        _item("LOCAL", local_only=True),
        _item("DENY", body="contains forbiddenword here"),
        _item("SEC", body=f"key {secret}"),
        _item("MAIL", body="contact someone@example.com"),
    ]
    bundle = _write_bundle(tmp_path, items)
    repo = _setup_repo(tmp_path, deny_terms="forbiddenword")
    llm = FakeLlm()
    assert _run(tmp_path, bundle, llm, tmp_path / "none", repo) == 0
    sent = str(llm.calls[0]["system"]) + str(llm.calls[0]["user"])
    assert "BODY_OK" in sent
    for i in ("CONF", "LOCAL", "DENY", "SEC", "MAIL"):
        assert f"TITLE_{i}" not in sent
    for body in (
        "BODY_CONF",
        "BODY_LOCAL",
        "forbiddenword",
        secret,
        "someone@example.com",
        "BODY_SEC",
        "BODY_MAIL",
    ):
        assert body not in sent


def test_ac7_written_articles_in_user(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, [_item("A"), _item("B"), _item("C")])
    sources = tmp_path / "sources_x"
    _write_source(sources, "one", "記事一の題材", ["J-1", "J-2"])
    _write_source(sources, "two", "記事二の題材", ["J-3"])
    llm = FakeLlm()
    assert _run(tmp_path, bundle, llm, sources) == 0
    user = str(llm.calls[0]["user"])
    for s in ("記事一の題材", "記事二の題材", "J-1", "J-2", "J-3"):
        assert s in user


def test_ac8_unknown_or_gated_ids_dropped(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, [_item("A"), _item("B"), _item("C", confidential=True)])
    cands = CandidateList(
        candidates=[
            _cand("UNKNOWN_CAND", ["A", "ZZZ"]),
            _cand("GATED_CAND", ["B", "C"]),
            _cand("GOOD_CAND", ["A", "B"]),
        ]
    )
    assert _run(tmp_path, bundle, FakeLlm(cands), tmp_path / "none") == 0
    js = (bundle.parent / "candidates.json").read_text(encoding="utf-8")
    md = (bundle.parent / "candidates.md").read_text(encoding="utf-8")
    for text in (js, md):
        assert "UNKNOWN_CAND" not in text
        assert "GATED_CAND" not in text
        assert "GOOD_CAND" in text


def test_ac9_written_subset_dropped(tmp_path: Path) -> None:
    ids = ["A", "B", "C", "D", "E"]
    bundle = _write_bundle(tmp_path, [_item(i) for i in ids])
    sources = tmp_path / "src"
    _write_source(sources, "one", "t1", ["A", "B"])
    _write_source(sources, "two", "t2", ["C", "D"])
    cands = CandidateList(
        candidates=[
            _cand("CAND_A", ["A"]),
            _cand("CAND_AB", ["A", "B"]),
            _cand("CAND_C", ["C"]),
            _cand("CAND_AE", ["A", "E"]),
            _cand("CAND_AC", ["A", "C"]),
        ]
    )
    assert _run(tmp_path, bundle, FakeLlm(cands), sources) == 0
    result = CandidateList.model_validate_json(
        (bundle.parent / "candidates.json").read_text(encoding="utf-8")
    )
    assert [c.title for c in result.candidates] == ["CAND_AE", "CAND_AC"]
    md = (bundle.parent / "candidates.md").read_text(encoding="utf-8")
    assert "CAND_AB" not in md and "CAND_C" not in md
    assert "CAND_AE" in md and "CAND_AC" in md


def test_ac10_ac11_markdown(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, [_item("ID-X"), _item("ID-Y"), _item("ID-Z")])
    cands = CandidateList(
        candidates=[
            _cand("題一", ["ID-X", "ID-Y"], "high", "理由一"),
            _cand("題二", ["ID-Z"], "low", "理由二"),
            _cand("題三", ["ID-X"], "medium", "理由三"),
        ]
    )
    assert _run(tmp_path, bundle, FakeLlm(cands), tmp_path / "none") == 0
    md = (bundle.parent / "candidates.md").read_text(encoding="utf-8")
    lines = md.splitlines()
    heads = [ln for ln in lines if ln.startswith("## ")]
    assert heads == ["## 1. 題一", "## 2. 題二", "## 3. 題三"]
    blocks = md.split("\n## ")[1:]
    assert "高" in blocks[0] and "理由一" in blocks[0] and "2 件" in blocks[0]
    assert "低" in blocks[1] and "理由二" in blocks[1] and "1 件" in blocks[1]
    assert "中" in blocks[2] and "理由三" in blocks[2] and "1 件" in blocks[2]
    for i in ("ID-X", "ID-Y", "ID-Z"):
        assert i not in md
        assert f"BODY_{i}" not in md


def test_ac12_empty_result(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, [_item("A")])
    assert _run(tmp_path, bundle, FakeLlm(), tmp_path / "none") == 0
    assert json.loads((bundle.parent / "candidates.json").read_text("utf-8")) == {
        "candidates": []
    }
    md = (bundle.parent / "candidates.md").read_text(encoding="utf-8")
    assert not any(ln.startswith("## ") for ln in md.splitlines())


def test_ac12_all_dropped(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, [_item("A")])
    llm = FakeLlm(CandidateList(candidates=[_cand("X", ["NOPE"])]))
    assert _run(tmp_path, bundle, llm, tmp_path / "none") == 0
    assert json.loads((bundle.parent / "candidates.json").read_text("utf-8")) == {
        "candidates": []
    }
    md = (bundle.parent / "candidates.md").read_text(encoding="utf-8")
    assert not any(ln.startswith("## ") for ln in md.splitlines())


def test_ac13_all_gated_and_ac17_no_leak(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _write_bundle(
        tmp_path, [_item("A", confidential=True), _item("B", local_only=True)]
    )
    llm = FakeLlm()
    assert _run(tmp_path, bundle, llm, tmp_path / "none") == 1
    assert llm.call_count == 0
    assert not (bundle.parent / "candidates.json").exists()
    assert not (bundle.parent / "candidates.md").exists()
    cap = capsys.readouterr()
    assert "BODY_" not in cap.out + cap.err


def test_ac14_bad_bundle(tmp_path: Path) -> None:
    llm = FakeLlm()
    repo = _setup_repo(tmp_path)
    assert _run(tmp_path, tmp_path / "missing.json", llm, None, repo) == 1
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert _run(tmp_path, broken, llm, None, repo) == 1
    empty = tmp_path / "empty.json"
    empty.write_text(
        json.dumps(
            {
                "topic": "t",
                "requested_by": "human",
                "items": [],
                "created_at": "2026-10-20T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    assert _run(tmp_path, empty, llm, None, repo) == 1
    assert llm.call_count == 0


def test_ac15_missing_sources_dir(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, [_item("A")])
    llm = FakeLlm()
    assert _run(tmp_path, bundle, llm, tmp_path / "does-not-exist") == 0
    assert llm.call_count == 1


@pytest.mark.parametrize(
    "content",
    ["{", '{"item_ids": "J-1"}', '{"item_ids": [1]}', "[]", '{"topic": "t"}'],
)
def test_ac16_bad_sources(tmp_path: Path, content: str) -> None:
    bundle = _write_bundle(tmp_path, [_item("A")])
    sources = tmp_path / "src"
    sources.mkdir()
    (sources / "bad.json").write_text(content, encoding="utf-8")
    llm = FakeLlm()
    assert _run(tmp_path, bundle, llm, sources) == 1
    assert llm.call_count == 0


def test_ac17_stdout_stderr_no_body_on_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _write_bundle(tmp_path, [_item("A"), _item("B", confidential=True)])
    assert _run(tmp_path, bundle, FakeLlm(), tmp_path / "none") == 0
    cap = capsys.readouterr()
    assert "BODY_" not in cap.out + cap.err


def test_ac18_default_sources_dir(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, [_item("A")])
    repo = _setup_repo(tmp_path)
    _write_source(repo / "sources", "one", "既定の題材", ["J-9"])
    llm = FakeLlm()
    assert _run(tmp_path, bundle, llm, None, repo) == 0
    user = str(llm.calls[0]["user"])
    assert "既定の題材" in user and "J-9" in user


def test_ac19_prompt_words() -> None:
    text = Path("prompts/system_candidates.md").read_text(encoding="utf-8")
    for w in ("技術", "設計", "個人的"):
        assert w in text
