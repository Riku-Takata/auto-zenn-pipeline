"""Publishing: remote checks, PR body, and the publish flow for sessions and articles."""

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from auto_zenn_pipeline.errors import AlreadyGeneratedError, PublishError
from auto_zenn_pipeline.models import Adr, SourceBundle
from auto_zenn_pipeline.runner import CommandResult, CommandRunner
from auto_zenn_pipeline.session import SessionRef

__all__ = [
    "CommandRunner",
    "PublishResult",
    "article_branch_exists",
    "build_pr_body",
    "build_sources_json",
    "draft_tag_exists",
    "publish",
    "publish_article",
    "remote_branch_exists",
    "remote_tag_exists",
]


@dataclass(frozen=True)
class PublishResult:
    branch: str
    tag: str
    commit_sha: str
    pr_url: str


def _run_at(
    cwd: Path,
    label: str,
    runner: CommandRunner,
    args: list[str],
) -> CommandResult:
    res = runner.run(args, cwd=cwd)
    if res.returncode != 0:
        raise PublishError(session=label, command=" ".join(args))
    return res


def _run(
    session: SessionRef,
    runner: CommandRunner,
    args: list[str],
) -> CommandResult:
    return _run_at(session.repo_root, session.name, runner, args)


def _ls_remote_exists_at(
    cwd: Path,
    label: str,
    args: list[str],
    runner: CommandRunner,
) -> bool:
    res = runner.run(args, cwd=cwd)
    if res.returncode == 0:
        return True
    if res.returncode == 2:
        return False
    raise PublishError(session=label, command=" ".join(args))


def _ls_remote_exists(
    session: SessionRef,
    args: list[str],
    runner: CommandRunner,
) -> bool:
    return _ls_remote_exists_at(session.repo_root, session.name, args, runner)


def remote_branch_exists(session: SessionRef, runner: CommandRunner) -> bool:
    args = [
        "git",
        "ls-remote",
        "--exit-code",
        "--heads",
        "origin",
        f"refs/heads/article/{session.name}",
    ]
    return _ls_remote_exists(session, args, runner)


def remote_tag_exists(
    session: SessionRef,
    tag: str,
    runner: CommandRunner,
) -> bool:
    args = [
        "git",
        "ls-remote",
        "--exit-code",
        "--tags",
        "origin",
        f"refs/tags/{tag}",
    ]
    return _ls_remote_exists(session, args, runner)


def article_branch_exists(
    repo_root: Path,
    slug: str,
    runner: CommandRunner,
) -> bool:
    args = [
        "git",
        "ls-remote",
        "--exit-code",
        "--heads",
        "origin",
        f"refs/heads/article/{slug}",
    ]
    return _ls_remote_exists_at(repo_root, slug, args, runner)


def draft_tag_exists(
    repo_root: Path,
    slug: str,
    runner: CommandRunner,
) -> bool:
    args = [
        "git",
        "ls-remote",
        "--exit-code",
        "--tags",
        "origin",
        f"refs/tags/draft/{slug}",
    ]
    return _ls_remote_exists_at(repo_root, slug, args, runner)


def build_pr_body(
    session: SessionRef,
    slug: str,
    adr: Adr,
    article_model: str,
) -> str:
    lines: list[str] = [
        "## Draft",
        f"- Session: `raw_logs/{session.name}`",
        f"- Slug: `{slug}`",
        f"- Tag: `draft/{slug}`",
        f"- Models: ADR extraction `{adr.model}`, article `{article_model}`",
        "",
        "## ADR summary",
        f"- Decision: {adr.decision.choice}",
        "- Options:",
    ]
    for opt in adr.options:
        lines.append(f"  - {opt.name}")
    lines.extend(
        [
            "",
            "マージすると公開されます(published: true)",
        ]
    )
    return "\n".join(lines) + "\n"


def _publish_files(
    *,
    cwd: Path,
    label: str,
    branch: str,
    tag: str,
    files: dict[str, str],
    commit_message: str,
    pr_title: str,
    pr_body: str,
    runner: CommandRunner,
) -> PublishResult:
    status = _run_at(cwd, label, runner, ["git", "status", "--porcelain"])
    if status.stdout.strip():
        raise PublishError(session=label, command="git status --porcelain")

    _run_at(cwd, label, runner, ["git", "switch", "-c", branch])

    try:
        for rel, content in files.items():
            p = cwd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(content)

        _run_at(cwd, label, runner, ["git", "add", "--", *files.keys()])
        _run_at(
            cwd,
            label,
            runner,
            [
                "git",
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "commit",
                "--author=github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>",
                "-m",
                commit_message,
            ],
        )
        _run_at(cwd, label, runner, ["git", "tag", tag])
        commit_sha = _run_at(
            cwd, label, runner, ["git", "rev-parse", "HEAD"]
        ).stdout.strip()
        _run_at(cwd, label, runner, ["git", "push", "origin", branch])
        _run_at(cwd, label, runner, ["git", "push", "origin", tag])

        fd, body_path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(pr_body)
            res = _run_at(
                cwd,
                label,
                runner,
                [
                    "gh",
                    "pr",
                    "create",
                    "--base",
                    "main",
                    "--head",
                    branch,
                    "--title",
                    pr_title,
                    "--body-file",
                    body_path,
                ],
            )
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(body_path)
        pr_url = res.stdout.strip()
    except BaseException:
        with contextlib.suppress(Exception):
            runner.run(["git", "switch", "-"], cwd=cwd)
        raise
    else:
        _run_at(cwd, label, runner, ["git", "switch", "-"])

    return PublishResult(
        branch=branch,
        tag=tag,
        commit_sha=commit_sha,
        pr_url=pr_url,
    )


def publish(
    session: SessionRef,
    slug: str,
    files: dict[str, str],
    pr_title: str,
    pr_body: str,
    runner: CommandRunner,
) -> PublishResult:
    return _publish_files(
        cwd=session.repo_root,
        label=session.name,
        branch=f"article/{session.name}",
        tag=f"draft/{slug}",
        files=files,
        commit_message=f"draft: {slug} ({session.name})",
        pr_title=pr_title,
        pr_body=pr_body,
        runner=runner,
    )


def build_sources_json(
    *,
    slug: str,
    bundle: SourceBundle,
    created_at: datetime,
) -> str:
    data = {
        "slug": slug,
        "topic": bundle.topic,
        "item_ids": [item.id for item in bundle.items],
        "created_at": created_at.isoformat(),
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _build_article_pr_body(
    *,
    title: str,
    slug: str,
    model: str,
    item_count: int,
) -> str:
    lines = [
        "## Draft",
        f"- Title: {title}",
        f"- Slug: `{slug}`",
        f"- Tag: `draft/{slug}`",
        f"- Model: `{model}`",
        f"- Sources: {item_count} 件(ID は `sources/{slug}.json`)",
        "",
        "マージすると公開されます(published: true)",
    ]
    return "\n".join(lines) + "\n"


def publish_article(
    *,
    repo_root: Path,
    slug: str,
    title: str,
    markdown: str,
    bundle: SourceBundle,
    model: str,
    created_at: datetime,
    runner: CommandRunner,
) -> PublishResult:
    branch = f"article/{slug}"
    tag = f"draft/{slug}"

    if article_branch_exists(repo_root, slug, runner):
        raise AlreadyGeneratedError(session=slug, ref=branch)

    files = {
        f"articles/{slug}.md": markdown,
        f"sources/{slug}.json": build_sources_json(
            slug=slug, bundle=bundle, created_at=created_at
        ),
    }
    pr_body = _build_article_pr_body(
        title=title, slug=slug, model=model, item_count=len(bundle.items)
    )
    return _publish_files(
        cwd=repo_root,
        label=slug,
        branch=branch,
        tag=tag,
        files=files,
        commit_message=f"draft: {slug}",
        pr_title=f"[draft] {title}",
        pr_body=pr_body,
        runner=runner,
    )
