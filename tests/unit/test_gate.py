"""Unit tests for publication gate (gate.py)."""

import datetime
from typing import Any

import pytest

from auto_zenn_pipeline.config import Config, LimitsConfig, ModelsConfig, PrivacyConfig
from auto_zenn_pipeline.errors import GateBlockedError, PipelineError
from auto_zenn_pipeline.gate import GateRemoval, GateResult, gate_bundle, gate_text
from auto_zenn_pipeline.models import SourceBundle, SourceItem, SourceKind


def make_config(
    exclude_repos: tuple[str, ...] = (),
    deny_terms: tuple[str, ...] = (),
) -> Config:
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
        privacy=PrivacyConfig(
            exclude_repos=exclude_repos,
            deny_terms=deny_terms,
        ),
    )


def make_item(
    id: str = "item-1",
    kind: SourceKind = "judgment",
    title: str = "Clean Title",
    body: str = "Clean Body",
    repo: str | None = "org/allowed-repo",
    confidential: bool = False,
    local_only: bool = False,
) -> SourceItem:
    return SourceItem(
        id=id,
        kind=kind,
        title=title,
        body=body,
        repo=repo,
        confidential=confidential,
        local_only=local_only,
    )


def make_bundle(
    items: list[SourceItem] | None = None,
    topic: str = "Testing Topic",
    requested_by: Any = "human",
    created_at: datetime.datetime | None = None,
) -> SourceBundle:
    if items is None:
        items = [make_item()]
    if created_at is None:
        created_at = datetime.datetime(2026, 10, 20, 10, 0, tzinfo=datetime.UTC)
    return SourceBundle(
        topic=topic,
        requested_by=requested_by,
        items=items,
        created_at=created_at,
    )


def test_gate_blocked_error_attrs() -> None:
    # AC-6
    assert issubclass(GateBlockedError, PipelineError)

    # positional
    err_pos = GateBlockedError("bundle", ["confidential", "secret"])
    assert err_pos.target == "bundle"
    assert err_pos.reasons == ("confidential", "secret")
    assert isinstance(err_pos.reasons, tuple)

    # keyword
    err_kw = GateBlockedError(target="text", reasons=("deny_term",))
    assert err_kw.target == "text"
    assert err_kw.reasons == ("deny_term",)
    assert isinstance(err_kw.reasons, tuple)


def test_gate_result_shape() -> None:
    # AC-7, AC-8
    config = make_config()
    clean_item = make_item(id="clean")
    confidential_item = make_item(id="conf", confidential=True)
    bundle = make_bundle(items=[clean_item, confidential_item])

    result = gate_bundle(bundle, config)
    assert isinstance(result, GateResult)
    assert hasattr(result, "bundle")
    assert hasattr(result, "removed")
    assert isinstance(result.removed, list)
    assert len(result.removed) == 1

    removal = result.removed[0]
    assert isinstance(removal, GateRemoval)
    assert removal.id == "conf"
    assert removal.reason == "confidential"

    valid_reasons = {
        "confidential",
        "local_only",
        "exclude_repo",
        "deny_term",
        "secret",
        "personal_info",
    }
    assert removal.reason in valid_reasons


def test_confidential_and_local_only_removed() -> None:
    # AC-9
    config = make_config()
    items = [
        make_item(id="c1", confidential=True),
        make_item(id="l1", local_only=True),
        make_item(id="ok"),
    ]
    bundle = make_bundle(items=items)
    result = gate_bundle(bundle, config)

    assert len(result.bundle.items) == 1
    assert result.bundle.items[0].id == "ok"
    assert len(result.removed) == 2
    assert result.removed[0] == GateRemoval(id="c1", reason="confidential")
    assert result.removed[1] == GateRemoval(id="l1", reason="local_only")


@pytest.mark.parametrize(
    "repo_val,title_val,body_val",
    [
        ("org/secret-repo", "Clean Title", "Clean Body"),
        ("Org/Secret-Repo", "Clean Title", "Clean Body"),
        ("org/other", "contains org/secret-repo here", "Clean Body"),
        ("org/other", "Clean Title", "contains Org/Secret-Repo here"),
    ],
)
def test_exclude_repo(repo_val: str, title_val: str, body_val: str) -> None:
    # AC-10
    config = make_config(exclude_repos=("org/secret-repo",))
    item = make_item(id="ex-repo", repo=repo_val, title=title_val, body=body_val)
    bundle = make_bundle(items=[item, make_item(id="ok")])
    result = gate_bundle(bundle, config)

    assert len(result.bundle.items) == 1
    assert result.bundle.items[0].id == "ok"
    assert result.removed == [GateRemoval(id="ex-repo", reason="exclude_repo")]


@pytest.mark.parametrize(
    "title_val,body_val",
    [
        ("Includes ProjectX", "Clean Body"),
        ("Clean Title", "Includes projectx inside"),
        ("Clean Title", "PROJECTX in caps"),
    ],
)
def test_deny_term(title_val: str, body_val: str) -> None:
    # AC-11
    config = make_config(deny_terms=("ProjectX",))
    item = make_item(id="denied", title=title_val, body=body_val)
    bundle = make_bundle(items=[item, make_item(id="ok")])
    result = gate_bundle(bundle, config)

    assert len(result.bundle.items) == 1
    assert result.bundle.items[0].id == "ok"
    assert result.removed == [GateRemoval(id="denied", reason="deny_term")]


@pytest.mark.parametrize(
    "secret_str",
    [
        "sk-ant-" + "a" * 30,
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "-----BEGIN RSA " + "PRIVATE KEY-----",
    ],
)
@pytest.mark.parametrize("target_field", ["title", "body"])
def test_secret_removed(secret_str: str, target_field: str) -> None:
    # AC-12
    config = make_config()
    title = (
        f"Some text {secret_str} more text"
        if target_field == "title"
        else "Clean Title"
    )
    body = (
        f"Some text {secret_str} more text" if target_field == "body" else "Clean Body"
    )
    item = make_item(id="secret-item", title=title, body=body)
    bundle = make_bundle(items=[item, make_item(id="ok")])
    result = gate_bundle(bundle, config)

    assert len(result.bundle.items) == 1
    assert result.bundle.items[0].id == "ok"
    assert result.removed == [GateRemoval(id="secret-item", reason="secret")]


@pytest.mark.parametrize(
    "pi_str",
    [
        "taro.yamada@example.com",
        "090-1234-5678",
        "03-1234-5678",
        "+81-90-1234-5678",
        "/Users/taro/",
        "/Users/taro/src/app",
        "/home/taro/",
        "/home/taro/work",
    ],
)
@pytest.mark.parametrize("target_field", ["title", "body"])
def test_personal_info_removed(pi_str: str, target_field: str) -> None:
    # AC-13
    config = make_config()
    title = f"Note: {pi_str} is private" if target_field == "title" else "Clean Title"
    body = f"Note: {pi_str} is private" if target_field == "body" else "Clean Body"
    item = make_item(id="pi-item", title=title, body=body)
    bundle = make_bundle(items=[item, make_item(id="ok")])
    result = gate_bundle(bundle, config)

    assert len(result.bundle.items) == 1
    assert result.bundle.items[0].id == "ok"
    assert result.removed == [GateRemoval(id="pi-item", reason="personal_info")]


@pytest.mark.parametrize(
    "safe_str",
    [
        "2026-10-20",
        "J-20261015-029",
        "R-20261020-100429-dba2",
        "T-025",
        "v1.2.3",
        "/usr/local/bin",
        "~/src",
    ],
)
def test_personal_info_not_matched(safe_str: str) -> None:
    # AC-14
    config = make_config()
    item = make_item(id="safe-item", title=safe_str, body=f"Details about {safe_str}")
    bundle = make_bundle(items=[item])
    result = gate_bundle(bundle, config)

    assert len(result.bundle.items) == 1
    assert result.bundle.items[0].id == "safe-item"
    assert result.removed == []


def test_reason_priority() -> None:
    # AC-15
    config = make_config(
        exclude_repos=("org/secret-repo",),
        deny_terms=("ProjectX",),
    )

    # confidential > personal_info
    item_conf = make_item(
        id="c",
        confidential=True,
        body="taro.yamada@example.com",
    )
    # local_only > exclude_repo
    item_local = make_item(
        id="l",
        local_only=True,
        repo="org/secret-repo",
    )
    # exclude_repo > deny_term
    item_ex = make_item(
        id="e",
        repo="org/secret-repo",
        body="ProjectX",
    )
    # deny_term > secret
    item_deny = make_item(
        id="d",
        title="ProjectX",
        body="sk-ant-" + "a" * 30,
    )
    # secret > personal_info
    item_sec = make_item(
        id="s",
        title="sk-ant-" + "a" * 30,
        body="090-1234-5678",
    )

    bundle = make_bundle(
        items=[item_conf, item_local, item_ex, item_deny, item_sec, make_item(id="ok")]
    )
    result = gate_bundle(bundle, config)

    assert [r.reason for r in result.removed] == [
        "confidential",
        "local_only",
        "exclude_repo",
        "deny_term",
        "secret",
    ]
    # 1 item has exactly 1 entry in removed
    assert len(result.removed) == 5
    assert len(result.bundle.items) == 1


def test_order_and_fields_preserved() -> None:
    # AC-16
    config = make_config()
    created_at = datetime.datetime(2026, 10, 20, 15, 30, tzinfo=datetime.UTC)
    item1 = make_item(id="k1", title="Kept 1")
    item2 = make_item(id="r1", title="Removed 1", confidential=True)
    item3 = make_item(id="k2", title="Kept 2")
    item4 = make_item(id="r2", title="Removed 2", local_only=True)
    item5 = make_item(id="k3", title="Kept 3")

    bundle = make_bundle(
        items=[item1, item2, item3, item4, item5],
        topic="My Topic",
        requested_by="candidate",
        created_at=created_at,
    )
    result = gate_bundle(bundle, config)

    assert [i.id for i in result.bundle.items] == ["k1", "k2", "k3"]
    assert [r.id for r in result.removed] == ["r1", "r2"]
    assert result.bundle.topic == "My Topic"
    assert result.bundle.requested_by == "candidate"
    assert result.bundle.created_at == created_at


def test_no_removal_equal_and_input_not_mutated() -> None:
    # AC-17
    config = make_config()
    items = [make_item(id="1"), make_item(id="2")]
    bundle = make_bundle(items=items)
    original_dump = bundle.model_dump()

    result = gate_bundle(bundle, config)
    assert result.bundle == bundle
    assert result.removed == []
    assert bundle.model_dump() == original_dump


def test_all_removed_raises() -> None:
    # AC-18
    config = make_config(
        exclude_repos=("org/secret-repo",),
        deny_terms=("ProjectX",),
    )
    items = [
        make_item(id="i1", confidential=True),
        make_item(id="i2", body="ProjectX"),
        make_item(id="i3", confidential=True),  # duplicate confidential
        make_item(id="i4", body="090-1234-5678"),
    ]
    bundle = make_bundle(items=items)

    with pytest.raises(GateBlockedError) as exc_info:
        gate_bundle(bundle, config)

    exc = exc_info.value
    assert exc.target == "bundle"
    assert exc.reasons == ("confidential", "deny_term", "personal_info")


def test_gate_text_clean() -> None:
    # AC-19
    config = make_config(
        exclude_repos=("org/secret-repo",),
        deny_terms=("ProjectX",),
    )
    clean_text = "This is a clean and harmless article body."
    gate_text(clean_text, config)


def test_gate_text_blocked() -> None:
    # AC-20
    config = make_config(
        exclude_repos=("org/secret-repo",),
        deny_terms=("ProjectX",),
    )

    # single reason: email
    with pytest.raises(GateBlockedError) as exc_info:
        gate_text("Contact me at taro.yamada@example.com for info", config)
    assert exc_info.value.target == "text"
    assert exc_info.value.reasons == ("personal_info",)

    # multiple reasons in text: exclude_repo, deny_term, secret, personal_info
    bad_text = (
        "Repo: org/secret-repo, Term: ProjectX, Key: "
        + ("AKIA" + "ABCDEFGHIJKLMNOP")
        + ", Phone: 090-1234-5678"
    )
    with pytest.raises(GateBlockedError) as exc_info:
        gate_text(bad_text, config)
    assert exc_info.value.target == "text"
    assert exc_info.value.reasons == (
        "exclude_repo",
        "deny_term",
        "secret",
        "personal_info",
    )


def test_body_not_leaked() -> None:
    # AC-21
    config = make_config(
        exclude_repos=("org/secret-repo",),
        deny_terms=("ProjectX",),
    )

    marker_body = "SECRET-BODY-MARKER-1234 taro.yamada@example.com"
    secret_body = "sk-ant-" + "a" * 30
    path_body = "/Users/taro/src/app"
    phone_body = "090-1234-5678"
    term_body = "ProjectX"

    leak_strings = [
        marker_body,
        "taro.yamada@example.com",
        secret_body,
        path_body,
        phone_body,
        term_body,
    ]

    item = make_item(
        id="leak-test-item",
        title="Check Leaks",
        body=f"{marker_body} {secret_body} {path_body} {phone_body} {term_body}",
    )
    bundle = make_bundle(items=[item, make_item(id="ok")])
    result = gate_bundle(bundle, config)

    for removal in result.removed:
        for s in leak_strings:
            assert s not in removal.id
            assert s not in removal.reason

    # test GateBlockedError from bundle
    bundle_all_bad = make_bundle(items=[item])
    with pytest.raises(GateBlockedError) as exc_info_bundle:
        gate_bundle(bundle_all_bad, config)
    exc_bundle = exc_info_bundle.value
    for s in leak_strings:
        assert s not in str(exc_bundle)
        for r in exc_bundle.reasons:
            assert s not in r

    # test GateBlockedError from text
    with pytest.raises(GateBlockedError) as exc_info_text:
        gate_text(item.body, config)
    exc_text = exc_info_text.value
    for s in leak_strings:
        assert s not in str(exc_text)
        for r in exc_text.reasons:
            assert s not in r
