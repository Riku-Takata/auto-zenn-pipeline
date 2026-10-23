"""Tests for the azp CLI entry point (gate and run)."""

import json
import subprocess
import tomllib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar, cast

import pytest

from auto_zenn_pipeline.article import (
    bundle_fallback_slug,
    choose_bundle_slug,
    render_markdown,
)
from auto_zenn_pipeline.cli import main
from auto_zenn_pipeline.errors import (
    AlreadyGeneratedError,
    LlmOutputError,
)
from auto_zenn_pipeline.llm import LlmClient
from auto_zenn_pipeline.models import ArticleDraft, SourceBundle, SourceItem
from auto_zenn_pipeline.publish import (
    article_branch_exists,
    draft_tag_exists,
)
from auto_zenn_pipeline.runner import CommandResult, CommandRunner, SubprocessRunner

T = TypeVar("T")


class FakeLlm(LlmClient):
    def __init__(
        self,
        draft: ArticleDraft | None = None,
        error: Exception | None = None,
    ) -> None:
        self.draft = draft
        self.error = error
        self.call_count = 0
        self.last_user: str | None = None
        self.last_system: str | None = None

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
        self.last_user = user
        self.last_system = system
        if self.error is not None:
            raise self.error
        assert self.draft is not None
        return cast(T, self.draft)


class RecordingRunner(CommandRunner):
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        input: str | None = None,
    ) -> CommandResult:
        self.calls.append((list(args), cwd))
        return CommandResult(returncode=0, stdout="", stderr="")


class HybridRunner(CommandRunner):
    def __init__(self, gh_returncode: int = 0) -> None:
        self.gh_returncode = gh_returncode
        self.calls: list[tuple[list[str], Path]] = []
        self.gh_calls: list[list[str]] = []
        self.push_calls: list[list[str]] = []
        self._subprocess_runner = SubprocessRunner()

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        input: str | None = None,
    ) -> CommandResult:
        call_args = list(args)
        self.calls.append((call_args, cwd))
        if (
            call_args
            and call_args[0] == "git"
            and len(call_args) > 1
            and call_args[1] == "push"
        ):
            self.push_calls.append(call_args)
        if call_args and call_args[0] == "gh":
            self.gh_calls.append(call_args)
            return CommandResult(
                returncode=self.gh_returncode,
                stdout="https://github.com/o/r/pull/1\n",
                stderr="",
            )
        return self._subprocess_runner.run(call_args, cwd=cwd, input=input)


def _setup_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo_pipeline = repo_root / "pipeline.toml"
    repo_pipeline.write_text(
        Path("pipeline.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    repo_prompts = repo_root / "prompts"
    repo_prompts.mkdir()
    for prompt_file in Path("prompts").glob("*.md"):
        (repo_prompts / prompt_file.name).write_text(
            prompt_file.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return repo_root


def _setup_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")

    origin_dir = tmp_path / "origin.git"
    origin_dir.mkdir()
    subprocess.run(["git", "init", "--bare", "-q"], cwd=origin_dir, check=True)

    repo_dir = _setup_repo(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin_dir)], cwd=repo_dir, check=True
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "main", "-q"], cwd=repo_dir, check=True
    )
    return repo_dir, origin_dir


def _make_bundle(
    *,
    items: list[SourceItem] | None = None,
    created_at: datetime | None = None,
    topic: str = "テストトピック",
) -> SourceBundle:
    if created_at is None:
        created_at = datetime(
            2026, 10, 20, 11, 46, 21, tzinfo=timezone(timedelta(hours=9))
        )
    if items is None:
        items = [
            SourceItem(
                id="item-1",
                kind="judgment",
                title="判断1",
                body="SECRET_BODY_ITEM_1",
                repo=None,
                confidential=False,
                local_only=False,
            ),
            SourceItem(
                id="item-2",
                kind="lesson",
                title="学び1",
                body="SECRET_BODY_ITEM_2",
                repo=None,
                confidential=False,
                local_only=False,
            ),
        ]
    return SourceBundle(
        topic=topic,
        requested_by="human",
        items=items,
        created_at=created_at,
    )


def _make_draft(
    slug: str = "valid-slug-12345",
    title: str = "テスト記事タイトル",
    body: str = "# 本文\n\nテスト内容です。",
) -> ArticleDraft:
    return ArticleDraft(
        title=title,
        emoji="📝",
        topics=["python", "cli"],
        slug=slug,
        body=body,
    )


def test_pyproject_registers_azp() -> None:
    # AC-1
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["project"]["scripts"]["azp"] == "auto_zenn_pipeline.cli:main"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown", "b.json"],
        ["gate"],
        ["run"],
        ["run", "b.json", "--bogus"],
    ],
)
def test_argument_errors_exit_2(argv: list[str]) -> None:
    # AC-3
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    assert exc_info.value.code == 2


def test_gate_prints_removed_ids_and_reasons(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-4
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle(
        items=[
            SourceItem(
                id="item-confidential",
                kind="judgment",
                title="秘密判断",
                body="SECRET_BODY_CONFIDENTIAL",
                repo=None,
                confidential=True,
                local_only=False,
            ),
            SourceItem(
                id="item-kept",
                kind="lesson",
                title="残る学び",
                body="SECRET_BODY_KEPT",
                repo=None,
                confidential=False,
                local_only=False,
            ),
        ]
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    code = main(["gate", str(bundle_path)], repo_root=repo_root)
    assert code == 0

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert any("item-confidential" in line and "confidential" in line for line in lines)
    assert "SECRET_BODY_CONFIDENTIAL" not in captured.out
    assert "SECRET_BODY_CONFIDENTIAL" not in captured.err
    assert "SECRET_BODY_KEPT" not in captured.out
    assert "SECRET_BODY_KEPT" not in captured.err


def test_gate_all_removed_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-5, AC-18
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle(
        items=[
            SourceItem(
                id="item-conf-1",
                kind="judgment",
                title="秘密1",
                body="SECRET_BODY_CONF_1",
                repo=None,
                confidential=True,
                local_only=False,
            ),
            SourceItem(
                id="item-conf-2",
                kind="lesson",
                title="秘密2",
                body="SECRET_BODY_CONF_2",
                repo=None,
                confidential=True,
                local_only=False,
            ),
        ]
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    code = main(["gate", str(bundle_path)], repo_root=repo_root)
    assert code == 1

    captured = capsys.readouterr()
    assert "GateBlockedError" in captured.err
    assert "SECRET_BODY_CONF_1" not in captured.out
    assert "SECRET_BODY_CONF_1" not in captured.err
    assert "SECRET_BODY_CONF_2" not in captured.out
    assert "SECRET_BODY_CONF_2" not in captured.err


def test_gate_does_not_call_llm(tmp_path: Path) -> None:
    # AC-6
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    fake_llm = FakeLlm(draft=_make_draft())
    code = main(["gate", str(bundle_path)], llm=fake_llm, repo_root=repo_root)
    assert code == 0
    assert fake_llm.call_count == 0


def test_run_no_publish_writes_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-2, AC-7
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    draft = _make_draft(slug="valid-slug-12345")
    fake_llm = FakeLlm(draft=draft)
    runner = RecordingRunner()

    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        runner=runner,
        repo_root=repo_root,
    )
    assert code == 0

    article_file = repo_root / "articles" / "valid-slug-12345.md"
    sources_file = repo_root / "sources" / "valid-slug-12345.json"
    assert article_file.exists()
    assert sources_file.exists()
    assert article_file.read_text(encoding="utf-8") == render_markdown(draft)

    guidelines_text = (repo_root / "prompts" / "writing_guidelines.md").read_text(
        encoding="utf-8"
    )
    assert fake_llm.last_user is not None
    assert guidelines_text in fake_llm.last_user


def test_run_no_publish_sources_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-8
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle(
        items=[
            SourceItem(
                id="item-first",
                kind="judgment",
                title="判断1",
                body="SECRET_BODY_FIRST",
                repo=None,
                confidential=False,
                local_only=False,
            ),
            SourceItem(
                id="item-conf",
                kind="lesson",
                title="除外項目",
                body="SECRET_BODY_CONF",
                repo=None,
                confidential=True,
                local_only=False,
            ),
            SourceItem(
                id="item-second",
                kind="principle",
                title="原則1",
                body="SECRET_BODY_SECOND",
                repo=None,
                confidential=False,
                local_only=False,
            ),
        ]
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    draft = _make_draft(slug="test-slug-json-123")
    fake_llm = FakeLlm(draft=draft)
    runner = RecordingRunner()

    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        runner=runner,
        repo_root=repo_root,
    )
    assert code == 0

    sources_file = repo_root / "sources" / "test-slug-json-123.json"
    sources_raw = sources_file.read_text(encoding="utf-8")
    sources_data = json.loads(sources_raw)

    assert set(sources_data.keys()) == {"slug", "topic", "item_ids", "created_at"}
    assert sources_data["slug"] == "test-slug-json-123"
    assert sources_data["topic"] == bundle.topic
    assert sources_data["item_ids"] == ["item-first", "item-second"]
    assert "SECRET_BODY_FIRST" not in sources_raw
    assert "SECRET_BODY_CONF" not in sources_raw
    assert "SECRET_BODY_SECOND" not in sources_raw


def test_run_no_publish_does_not_use_runner(tmp_path: Path) -> None:
    # AC-9
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    fake_llm = FakeLlm(draft=_make_draft())
    runner = RecordingRunner()

    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        runner=runner,
        repo_root=repo_root,
    )
    assert code == 0
    assert len(runner.calls) == 0


def test_bundle_fallback_slug_utc() -> None:
    # AC-10 (+09:00 -> UTC)
    created_at = datetime(2026, 10, 20, 11, 46, 21, tzinfo=timezone(timedelta(hours=9)))
    bundle = _make_bundle(created_at=created_at)
    assert bundle_fallback_slug(bundle) == "bundle-20261020-024621"


def test_run_no_publish_invalid_slug_uses_fallback(tmp_path: Path) -> None:
    # AC-10
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle(
        created_at=datetime(
            2026, 10, 20, 11, 46, 21, tzinfo=timezone(timedelta(hours=9))
        )
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    draft = _make_draft(slug="short")
    fake_llm = FakeLlm(draft=draft)

    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        repo_root=repo_root,
    )
    assert code == 0

    assert (repo_root / "articles" / "bundle-20261020-024621.md").exists()
    assert (repo_root / "sources" / "bundle-20261020-024621.json").exists()


def test_run_no_publish_existing_article_uses_fallback(tmp_path: Path) -> None:
    # AC-10
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle(
        created_at=datetime(
            2026, 10, 20, 11, 46, 21, tzinfo=timezone(timedelta(hours=9))
        )
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    articles_dir = repo_root / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    existing_article = articles_dir / "already-exists-slug.md"
    existing_article.write_text("existing", encoding="utf-8")

    draft = _make_draft(slug="already-exists-slug")
    fake_llm = FakeLlm(draft=draft)

    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        repo_root=repo_root,
    )
    assert code == 0

    assert (repo_root / "articles" / "bundle-20261020-024621.md").exists()
    assert (repo_root / "sources" / "bundle-20261020-024621.json").exists()


def test_run_no_publish_fallback_exists_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-10, AC-18
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle(
        created_at=datetime(
            2026, 10, 20, 11, 46, 21, tzinfo=timezone(timedelta(hours=9))
        )
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    articles_dir = repo_root / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    (articles_dir / "bundle-20261020-024621.md").write_text(
        "existing", encoding="utf-8"
    )

    initial_articles = set(articles_dir.glob("*.md"))
    sources_dir = repo_root / "sources"

    draft = _make_draft(slug="short")
    fake_llm = FakeLlm(draft=draft)

    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        repo_root=repo_root,
    )
    assert code == 1

    captured = capsys.readouterr()
    assert "AlreadyGeneratedError" in captured.err

    # ファイルが増えていない
    assert set(articles_dir.glob("*.md")) == initial_articles
    assert not sources_dir.exists() or len(list(sources_dir.glob("*.json"))) == 0


def test_run_all_removed_returns_1_without_llm(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-11, AC-18
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle(
        items=[
            SourceItem(
                id="conf-1",
                kind="judgment",
                title="タイトル",
                body="SECRET_BODY",
                repo=None,
                confidential=True,
                local_only=False,
            )
        ]
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    fake_llm = FakeLlm(draft=_make_draft())

    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        repo_root=repo_root,
    )
    assert code == 1
    assert fake_llm.call_count == 0

    assert not (repo_root / "articles").exists()
    assert not (repo_root / "sources").exists()

    captured = capsys.readouterr()
    assert "GateBlockedError" in captured.err


def test_run_article_with_email_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-12, AC-18
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    draft = _make_draft(body="# 本文\n\n連絡先: someone@example.com")
    fake_llm = FakeLlm(draft=draft)

    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        repo_root=repo_root,
    )
    assert code == 1

    assert not (repo_root / "articles").exists()
    assert not (repo_root / "sources").exists()

    captured = capsys.readouterr()
    assert "GateBlockedError" in captured.err


def test_run_llm_output_error_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-13, AC-18
    repo_root = _setup_repo(tmp_path)
    bundle = _make_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    fake_llm = FakeLlm(error=LlmOutputError("bundle", "article"))

    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        repo_root=repo_root,
    )
    assert code == 1

    assert not (repo_root / "articles").exists()
    assert not (repo_root / "sources").exists()

    captured = capsys.readouterr()
    assert "LlmOutputError" in captured.err


@pytest.mark.parametrize("command", ["gate", "run"])
@pytest.mark.parametrize(
    "error_kind",
    ["missing_file", "broken_json", "invalid_kind"],
)
def test_bundle_errors_return_1(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
    error_kind: str,
) -> None:
    # AC-14, AC-18
    repo_root = _setup_repo(tmp_path)
    bundle_path = tmp_path / "test_bundle.json"

    secret_marker = "SECRET_MARKER_TEST_12345"

    if error_kind == "missing_file":
        pass  # bundle_path does not exist
    elif error_kind == "broken_json":
        bundle_path.write_text("{ broken json " + secret_marker, encoding="utf-8")
    elif error_kind == "invalid_kind":
        bad_json = {
            "topic": "テスト",
            "requested_by": "human",
            "items": [
                {
                    "id": "item-1",
                    "kind": "unknown_invalid_kind",
                    "title": "タイトル",
                    "body": secret_marker,
                    "repo": None,
                    "confidential": False,
                    "local_only": False,
                }
            ],
            "created_at": datetime.now(UTC).isoformat(),
        }
        bundle_path.write_text(json.dumps(bad_json), encoding="utf-8")

    argv = [command, str(bundle_path)]
    if command == "run":
        argv.append("--no-publish")

    fake_llm = FakeLlm(draft=_make_draft())
    code = main(argv, llm=fake_llm, repo_root=repo_root)
    assert code == 1

    captured = capsys.readouterr()
    assert "BundleError" in captured.err
    assert secret_marker not in captured.out
    assert secret_marker not in captured.err


def test_run_does_not_print_bodies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-15
    repo_root = _setup_repo(tmp_path)
    body_kept = "BODY_KEPT_TOKEN_8921"
    body_removed = "BODY_REMOVED_TOKEN_4310"
    bundle = _make_bundle(
        items=[
            SourceItem(
                id="item-kept",
                kind="judgment",
                title="残る",
                body=body_kept,
                repo=None,
                confidential=False,
                local_only=False,
            ),
            SourceItem(
                id="item-removed",
                kind="lesson",
                title="外れる",
                body=body_removed,
                repo=None,
                confidential=True,
                local_only=False,
            ),
        ]
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    fake_llm = FakeLlm(draft=_make_draft())
    code = main(
        ["run", str(bundle_path), "--no-publish"],
        llm=fake_llm,
        repo_root=repo_root,
    )
    assert code == 0

    captured = capsys.readouterr()
    assert body_kept not in captured.out
    assert body_kept not in captured.err
    assert body_removed not in captured.out
    assert body_removed not in captured.err


def test_run_publish_creates_branch_and_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # AC-16
    repo_dir, origin_dir = _setup_git_repo(tmp_path, monkeypatch)
    body_token = "BODY_PUBLISH_SECRET_9871"
    bundle = _make_bundle(
        items=[
            SourceItem(
                id="item-1",
                kind="judgment",
                title="判断",
                body=body_token,
                repo=None,
                confidential=False,
                local_only=False,
            )
        ]
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    slug = "publish-slug-12345"
    fake_llm = FakeLlm(draft=_make_draft(slug=slug))
    runner = HybridRunner()

    code = main(
        ["run", str(bundle_path)],
        llm=fake_llm,
        runner=runner,
        repo_root=repo_dir,
    )
    assert code == 0

    # origin check
    branch_ref = f"refs/heads/article/{slug}"
    tag_ref = f"refs/tags/draft/{slug}"
    branch_sha = subprocess.run(
        ["git", "rev-parse", branch_ref],
        cwd=origin_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tag_sha = subprocess.run(
        ["git", "rev-parse", tag_ref],
        cwd=origin_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch_sha == tag_sha
    assert len(branch_sha) == 40

    assert len(runner.gh_calls) == 1
    assert "pr" in runner.gh_calls[0]
    assert "create" in runner.gh_calls[0]

    captured = capsys.readouterr()
    assert body_token not in captured.out
    assert body_token not in captured.err


def test_run_publish_existing_fallback_branch_stops_before_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # AC-17, AC-18
    repo_dir, _origin_dir = _setup_git_repo(tmp_path, monkeypatch)
    created_at = datetime(2026, 10, 20, 11, 46, 21, tzinfo=timezone(timedelta(hours=9)))
    fallback_slug = "bundle-20261020-024621"

    # Push article/<fallback_slug> branch to origin beforehand
    subprocess.run(
        ["git", "branch", f"article/{fallback_slug}"], cwd=repo_dir, check=True
    )
    subprocess.run(
        ["git", "push", "origin", f"article/{fallback_slug}", "-q"],
        cwd=repo_dir,
        check=True,
    )

    body_token = "BODY_STOP_BEFORE_LLM_4431"
    bundle = _make_bundle(
        created_at=created_at,
        items=[
            SourceItem(
                id="item-1",
                kind="judgment",
                title="判断",
                body=body_token,
                repo=None,
                confidential=False,
                local_only=False,
            )
        ],
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    fake_llm = FakeLlm(draft=_make_draft())
    runner = HybridRunner()

    code = main(
        ["run", str(bundle_path)],
        llm=fake_llm,
        runner=runner,
        repo_root=repo_dir,
    )
    assert code == 1
    assert fake_llm.call_count == 0
    assert len(runner.push_calls) == 0
    assert len(runner.gh_calls) == 0

    captured = capsys.readouterr()
    assert "AlreadyGeneratedError" in captured.err
    assert body_token not in captured.out
    assert body_token not in captured.err


def test_remote_exists_and_choose_bundle_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir, _origin_dir = _setup_git_repo(tmp_path, monkeypatch)
    runner = SubprocessRunner()

    assert not article_branch_exists(repo_dir, "test-slug-foo", runner)
    assert not draft_tag_exists(repo_dir, "test-slug-foo", runner)

    # push a branch and a tag to origin
    subprocess.run(["git", "branch", "article/test-slug-foo"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "push", "origin", "article/test-slug-foo", "-q"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(["git", "tag", "draft/test-slug-foo"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "push", "origin", "draft/test-slug-foo", "-q"], cwd=repo_dir, check=True
    )

    assert article_branch_exists(repo_dir, "test-slug-foo", runner)
    assert draft_tag_exists(repo_dir, "test-slug-foo", runner)

    # choose_bundle_slug test
    bundle = _make_bundle(
        created_at=datetime(
            2026, 10, 20, 11, 46, 21, tzinfo=timezone(timedelta(hours=9))
        )
    )
    valid_draft = _make_draft(slug="valid-new-slug")
    assert choose_bundle_slug(valid_draft, bundle, lambda s: False) == "valid-new-slug"

    # slug already exists -> fallback
    assert (
        choose_bundle_slug(valid_draft, bundle, lambda s: s == "valid-new-slug")
        == "bundle-20261020-024621"
    )

    # both slug and fallback exist -> AlreadyGeneratedError
    with pytest.raises(AlreadyGeneratedError):
        choose_bundle_slug(valid_draft, bundle, lambda s: True)
