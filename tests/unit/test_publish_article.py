import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auto_zenn_pipeline.errors import AlreadyGeneratedError
from auto_zenn_pipeline.models import SourceBundle, SourceItem
from auto_zenn_pipeline.publish import (
    CommandRunner,
    PublishResult,
    build_sources_json,
    publish_article,
)
from auto_zenn_pipeline.runner import CommandResult, SubprocessRunner


class HybridRunner(CommandRunner):
    def __init__(self, gh_returncode: int = 0) -> None:
        self.gh_returncode = gh_returncode
        self.calls: list[tuple[list[str], Path]] = []
        self.gh_calls: list[list[str]] = []
        self.body_path: str | None = None
        self.body_at_call: str | None = None
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
        if call_args and call_args[0] == "gh":
            self.gh_calls.append(call_args)
            if "--body-file" in call_args:
                idx = call_args.index("--body-file")
                if idx + 1 < len(call_args):
                    self.body_path = call_args[idx + 1]
                    with open(self.body_path, encoding="utf-8", newline="") as f:
                        self.body_at_call = f.read()
            return CommandResult(
                returncode=self.gh_returncode,
                stdout="  https://github.com/o/r/pull/1\n",
                stderr="" if self.gh_returncode == 0 else "gh error",
            )
        return self._subprocess_runner.run(call_args, cwd=cwd, input=input)


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")

    origin_dir = tmp_path / "origin.git"
    origin_dir.mkdir()
    subprocess.run(["git", "init", "--bare", "-q"], cwd=origin_dir, check=True)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin_dir)],
        cwd=repo_dir,
        check=True,
    )
    return repo_dir, origin_dir


def _make_bundle() -> SourceBundle:
    return SourceBundle(
        topic="設計の判断を記事にする",
        requested_by="human",
        items=[
            SourceItem(
                id="J-0101",
                kind="judgment",
                title="判断1",
                body="BODY-ALPHA-7f3a",
                repo=None,
                confidential=False,
                local_only=False,
            ),
            SourceItem(
                id="L-0202",
                kind="lesson",
                title="学び1",
                body="BODY-BETA-9c1d",
                repo=None,
                confidential=False,
                local_only=False,
            ),
            SourceItem(
                id="PR-0303",
                kind="principle",
                title="原則1",
                body="BODY-GAMMA-2e8b",
                repo=None,
                confidential=False,
                local_only=False,
            ),
        ],
        created_at=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
    )


def test_importable() -> None:
    # AC-1
    from auto_zenn_pipeline.publish import build_sources_json, publish_article

    assert callable(publish_article)
    assert callable(build_sources_json)


def test_publish_article_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-2 ~ AC-10
    repo_dir, origin_dir = _setup(tmp_path, monkeypatch)
    main_sha = _git(repo_dir, "rev-parse", "HEAD")

    slug = "knowledge-article-1"
    title = "判断の記事"
    markdown = "# 判断の記事\r\n\r\nこれは本文です。\n"
    bundle = _make_bundle()
    model = "claude-opus-5-5"
    created_at = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    runner = HybridRunner()
    result = publish_article(
        repo_root=repo_dir,
        slug=slug,
        title=title,
        markdown=markdown,
        bundle=bundle,
        model=model,
        created_at=created_at,
        runner=runner,
    )

    # AC-2
    assert isinstance(result, PublishResult)
    assert result.branch == "article/knowledge-article-1"
    assert result.tag == "draft/knowledge-article-1"
    assert result.pr_url == "https://github.com/o/r/pull/1"

    # AC-3
    branch_ref = "refs/heads/article/knowledge-article-1"
    tag_ref = "refs/tags/draft/knowledge-article-1"
    origin_branch_sha = _git(origin_dir, "rev-parse", branch_ref)
    origin_tag_sha = _git(origin_dir, "rev-parse", tag_ref)
    assert origin_branch_sha == origin_tag_sha
    assert origin_tag_sha == result.commit_sha
    assert len(result.commit_sha) == 40

    # AC-4
    parent_sha = _git(origin_dir, "rev-parse", f"{result.commit_sha}^")
    assert parent_sha == main_sha

    diff_lines = _git(
        origin_dir,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        result.commit_sha,
    ).splitlines()
    expected_diff = {
        "A\tarticles/knowledge-article-1.md",
        "A\tsources/knowledge-article-1.json",
    }
    assert set(diff_lines) == expected_diff
    assert len(diff_lines) == 2

    # AC-5
    show_md_res = subprocess.run(
        ["git", "show", f"{result.commit_sha}:articles/knowledge-article-1.md"],
        cwd=origin_dir,
        check=True,
        capture_output=True,
    )
    assert show_md_res.stdout == markdown.encode("utf-8")

    # AC-6
    show_json_res = subprocess.run(
        ["git", "show", f"{result.commit_sha}:sources/knowledge-article-1.json"],
        cwd=origin_dir,
        check=True,
        capture_output=True,
    )
    sources_dict = json.loads(show_json_res.stdout.decode("utf-8"))
    assert set(sources_dict.keys()) == {"slug", "topic", "item_ids", "created_at"}
    assert sources_dict["slug"] == "knowledge-article-1"
    assert sources_dict["topic"] == "設計の判断を記事にする"
    assert sources_dict["item_ids"] == ["J-0101", "L-0202", "PR-0303"]
    assert datetime.fromisoformat(sources_dict["created_at"]) == created_at

    # AC-7
    sources_text = show_json_res.stdout.decode("utf-8")
    for prohibited in ["BODY-ALPHA-7f3a", "BODY-BETA-9c1d", "BODY-GAMMA-2e8b"]:
        assert prohibited not in sources_text

    # AC-8
    assert len(runner.gh_calls) == 1
    gh_args = runner.gh_calls[0]
    assert gh_args[0] == "gh"
    pr_idx = gh_args.index("pr")
    create_idx = gh_args.index("create")
    assert pr_idx < create_idx
    base_idx = gh_args.index("--base")
    assert gh_args[base_idx + 1] == "main"
    head_idx = gh_args.index("--head")
    assert gh_args[head_idx + 1] == "article/knowledge-article-1"
    body_idx = gh_args.index("--body-file")
    assert gh_args[body_idx + 1] == runner.body_path
    assert runner.body_at_call is not None

    for expected in [
        "判断の記事",
        "knowledge-article-1",
        "draft/knowledge-article-1",
        "claude-opus-5-5",
        "3",
        "published: true",
    ]:
        assert expected in runner.body_at_call

    # AC-9
    for prohibited in [
        "J-0101",
        "L-0202",
        "PR-0303",
        "BODY-ALPHA-7f3a",
        "BODY-BETA-9c1d",
        "BODY-GAMMA-2e8b",
    ]:
        assert prohibited not in runner.body_at_call

    # AC-10
    current_branch = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    assert current_branch == "main"


def test_publish_article_already_generated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-11
    repo_dir, origin_dir = _setup(tmp_path, monkeypatch)
    subprocess.run(
        ["git", "branch", "article/knowledge-article-1"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(
        ["git", "push", "origin", "article/knowledge-article-1", "-q"],
        cwd=repo_dir,
        check=True,
    )

    runner = HybridRunner()
    bundle = _make_bundle()
    created_at = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    with pytest.raises(AlreadyGeneratedError) as exc_info:
        publish_article(
            repo_root=repo_dir,
            slug="knowledge-article-1",
            title="判断の記事",
            markdown="# 本文\n",
            bundle=bundle,
            model="claude-opus-5-5",
            created_at=created_at,
            runner=runner,
        )

    assert exc_info.value.ref == "article/knowledge-article-1"
    assert len(runner.gh_calls) == 0

    origin_tags = _git(origin_dir, "tag", "--list", "draft/knowledge-article-1")
    assert origin_tags == ""

    current_branch = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    assert current_branch == "main"

    assert not (repo_dir / "articles" / "knowledge-article-1.md").exists()
    assert not (repo_dir / "sources" / "knowledge-article-1.json").exists()


def test_build_sources_json() -> None:
    # AC-12
    bundle = _make_bundle()
    created_at = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    result = build_sources_json(slug="s-1", bundle=bundle, created_at=created_at)

    assert isinstance(result, str)
    data = json.loads(result)
    assert set(data.keys()) == {"slug", "topic", "item_ids", "created_at"}
    assert data["slug"] == "s-1"
    assert data["topic"] == bundle.topic
    assert data["item_ids"] == ["J-0101", "L-0202", "PR-0303"]
    assert datetime.fromisoformat(data["created_at"]) == created_at

    for prohibited in ["BODY-ALPHA-7f3a", "BODY-BETA-9c1d", "BODY-GAMMA-2e8b"]:
        assert prohibited not in result
