"""Publishing: remote checks, PR body, and the publish flow."""

import contextlib
import os
import tempfile
from dataclasses import dataclass

from auto_zenn_pipeline.errors import PublishError
from auto_zenn_pipeline.models import Adr
from auto_zenn_pipeline.runner import CommandResult, CommandRunner
from auto_zenn_pipeline.session import SessionRef

__all__ = [
    "CommandRunner",
    "PublishResult",
    "build_pr_body",
    "publish",
    "remote_branch_exists",
    "remote_tag_exists",
]


@dataclass(frozen=True)
class PublishResult:
    branch: str
    tag: str
    commit_sha: str
    pr_url: str


def _run(
    session: SessionRef,
    runner: CommandRunner,
    args: list[str],
) -> CommandResult:
    res = runner.run(args, cwd=session.repo_root)
    if res.returncode != 0:
        raise PublishError(session=session.name, command=" ".join(args))
    return res


def _ls_remote_exists(
    session: SessionRef,
    args: list[str],
    runner: CommandRunner,
) -> bool:
    res = runner.run(args, cwd=session.repo_root)
    if res.returncode == 0:
        return True
    if res.returncode == 2:
        return False
    raise PublishError(session=session.name, command=" ".join(args))


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


def publish(
    session: SessionRef,
    slug: str,
    files: dict[str, str],
    pr_title: str,
    pr_body: str,
    runner: CommandRunner,
) -> PublishResult:
    branch = f"article/{session.name}"
    tag = f"draft/{slug}"

    status = _run(session, runner, ["git", "status", "--porcelain"])
    if status.stdout.strip():
        raise PublishError(session=session.name, command="git status --porcelain")

    _run(session, runner, ["git", "switch", "-c", branch])

    try:
        for rel, content in files.items():
            p = session.repo_root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(content)

        _run(session, runner, ["git", "add", "--", *files.keys()])
        _run(
            session,
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
                f"draft: {slug} ({session.name})",
            ],
        )
        _run(session, runner, ["git", "tag", tag])
        commit_sha = _run(session, runner, ["git", "rev-parse", "HEAD"]).stdout.strip()
        _run(session, runner, ["git", "push", "origin", branch])
        _run(session, runner, ["git", "push", "origin", tag])

        fd, body_path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(pr_body)
            res = _run(
                session,
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
            runner.run(["git", "switch", "-"], cwd=session.repo_root)
        raise
    else:
        _run(session, runner, ["git", "switch", "-"])

    return PublishResult(
        branch=branch,
        tag=tag,
        commit_sha=commit_sha,
        pr_url=pr_url,
    )

