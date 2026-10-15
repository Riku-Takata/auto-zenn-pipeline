"""Command line entry point: one session flow with ``--no-publish``."""

import argparse
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from auto_zenn_pipeline.adr import build_adr, dump_adr, extract_adr
from auto_zenn_pipeline.article import choose_slug, render_markdown, write_article
from auto_zenn_pipeline.config import load_config
from auto_zenn_pipeline.errors import AlreadyGeneratedError, PublishError
from auto_zenn_pipeline.llm import AnthropicLlmClient, LlmClient
from auto_zenn_pipeline.render import check_size, render_logs
from auto_zenn_pipeline.runner import CommandRunner, SubprocessRunner
from auto_zenn_pipeline.secret_scan import scan
from auto_zenn_pipeline.session import load_session, resolve_session


def _escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="generate_article.py")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", metavar="PATH")
    group.add_argument("--before", metavar="SHA")
    parser.add_argument("--after", metavar="SHA")
    parser.add_argument("--no-publish", action="store_true")
    return parser


def _run_session(
    path: str,
    display: list[str],
    *,
    get_llm: Callable[[], LlmClient],
    runner: CommandRunner,
    repo_root: Path,
) -> None:
    config = load_config(repo_root / "pipeline.toml")
    ref = resolve_session(repo_root, path)
    name = ref.name
    display[0] = name
    logs = load_session(ref)

    adr_path = repo_root / "raw_logs" / name / "adr.json"
    if adr_path.exists():
        raise AlreadyGeneratedError(session=name, ref=f"raw_logs/{name}/adr.json")

    scan(logs)

    rendered = render_logs(logs)
    check_size(name, rendered, config.limits.max_log_chars)

    prompts = repo_root / "prompts"
    system_adr = (prompts / "system_adr_extractor.md").read_text(encoding="utf-8")
    extraction = extract_adr(
        llm=get_llm(),
        config=config,
        session=name,
        rendered=rendered,
        system_prompt=system_adr,
    )
    if not extraction.worth_writing:
        reason = _escape(extraction.skip_reason or "")
        print(f"::notice title=skipped {name}::{reason}")
        return

    res = runner.run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    if res.returncode != 0:
        raise PublishError(session=name, command="git rev-parse HEAD")
    adr = build_adr(
        extraction,
        session=name,
        source_commit=res.stdout.strip(),
        model=config.models.adr_extraction,
        generated_at=datetime.now(UTC),
    )
    guidelines = (prompts / "writing_guidelines.md").read_text(encoding="utf-8")
    system_gen = (prompts / "system_generator.md").read_text(encoding="utf-8")
    draft = write_article(
        llm=get_llm(),
        config=config,
        adr=adr,
        guidelines=guidelines,
        system_prompt=system_gen,
    )

    articles_dir = repo_root / "articles"
    slug = choose_slug(draft, ref, lambda s: (articles_dir / f"{s}.md").exists())

    articles_dir.mkdir(parents=True, exist_ok=True)
    (articles_dir / f"{slug}.md").write_text(render_markdown(draft), encoding="utf-8")
    adr_path.write_text(dump_adr(adr), encoding="utf-8")


def main(
    argv: list[str] | None = None,
    *,
    llm: LlmClient | None = None,
    runner: CommandRunner | None = None,
    repo_root: Path | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.after is not None and args.before is None:
        parser.error("--after requires --before")
    if args.before is not None:
        parser.error("--before is not supported yet")
    if not args.no_publish:
        parser.error("--no-publish is required for now")

    root = repo_root if repo_root is not None else Path.cwd()
    run = runner if runner is not None else SubprocessRunner()
    cache: list[LlmClient] = []

    def get_llm() -> LlmClient:
        if not cache:
            cache.append(llm if llm is not None else AnthropicLlmClient())
        return cache[0]

    display = [args.session]
    try:
        _run_session(
            args.session, display, get_llm=get_llm, runner=run, repo_root=root
        )
    except Exception as exc:
        print(f"::error title={type(exc).__name__} {display[0]}::{_escape(str(exc))}")
        return 1
    return 0
