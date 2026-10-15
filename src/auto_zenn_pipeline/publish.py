"""Publishing helpers: remote checks and PR body."""

from auto_zenn_pipeline.errors import PublishError
from auto_zenn_pipeline.models import Adr
from auto_zenn_pipeline.runner import CommandRunner
from auto_zenn_pipeline.session import SessionRef

__all__ = [
    "CommandRunner",
    "build_pr_body",
    "remote_branch_exists",
    "remote_tag_exists",
]


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
