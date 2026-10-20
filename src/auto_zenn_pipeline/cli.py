"""Command line entry point azp: gate and run for a source bundle."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from auto_zenn_pipeline.article import (
    bundle_fallback_slug,
    choose_bundle_slug,
    render_markdown,
    write_article,
)
from auto_zenn_pipeline.config import load_config
from auto_zenn_pipeline.errors import (
    AlreadyGeneratedError,
    BundleError,
    PipelineError,
)
from auto_zenn_pipeline.gate import GateResult, gate_bundle
from auto_zenn_pipeline.llm import ClaudeCliClient, LlmClient
from auto_zenn_pipeline.models import SourceBundle
from auto_zenn_pipeline.publish import (
    article_branch_exists,
    build_sources_json,
    draft_tag_exists,
    publish_article,
)
from auto_zenn_pipeline.runner import CommandRunner, SubprocessRunner

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="azp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gate_parser = subparsers.add_parser("gate")
    gate_parser.add_argument("bundle", help="Path to source bundle JSON")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("bundle", help="Path to source bundle JSON")
    run_parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Write files without publishing",
    )

    return parser


def _load_bundle(path: str) -> SourceBundle:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BundleError(path, "not found") from None
    except (OSError, UnicodeDecodeError):
        raise BundleError(path, "unreadable") from None

    try:
        return SourceBundle.model_validate_json(text)
    except (ValidationError, ValueError):
        raise BundleError(path, "invalid") from None


def _print_removed(result: GateResult) -> None:
    for r in result.removed:
        print(f"removed\t{r.id}\t{r.reason}")


def _gate(bundle_path: str, root: Path) -> None:
    config = load_config(root / "pipeline.toml")
    bundle = _load_bundle(bundle_path)
    result = gate_bundle(bundle, config)
    _print_removed(result)


def _run(
    bundle_path: str,
    *,
    no_publish: bool,
    root: Path,
    runner: CommandRunner,
    llm: LlmClient | None,
) -> None:
    config = load_config(root / "pipeline.toml")
    bundle = _load_bundle(bundle_path)
    result = gate_bundle(bundle, config)
    _print_removed(result)
    gated = result.bundle

    fallback = bundle_fallback_slug(gated)
    if not no_publish and article_branch_exists(root, fallback, runner):
        raise AlreadyGeneratedError(session=fallback, ref=f"article/{fallback}")

    guidelines = (root / "prompts" / "writing_guidelines.md").read_text(
        encoding="utf-8"
    )
    client = llm if llm is not None else ClaudeCliClient(runner=runner, cwd=root)
    draft = write_article(
        llm=client, config=config, bundle=gated, guidelines=guidelines
    )

    def exists(s: str) -> bool:
        if (root / "articles" / f"{s}.md").exists():
            return True
        return not no_publish and draft_tag_exists(root, s, runner)

    slug = choose_bundle_slug(draft, gated, exists)
    markdown = render_markdown(draft)
    now = datetime.now(UTC)

    if no_publish:
        sources_json = build_sources_json(slug=slug, bundle=gated, created_at=now)
        articles_dir = root / "articles"
        sources_dir = root / "sources"
        articles_dir.mkdir(parents=True, exist_ok=True)
        sources_dir.mkdir(parents=True, exist_ok=True)
        (articles_dir / f"{slug}.md").write_text(markdown, encoding="utf-8")
        (sources_dir / f"{slug}.json").write_text(sources_json, encoding="utf-8")
        print(f"articles/{slug}.md")
        print(f"sources/{slug}.json")
        return

    pub_result = publish_article(
        repo_root=root,
        slug=slug,
        title=draft.title,
        markdown=markdown,
        bundle=gated,
        model=config.models.article_writing,
        created_at=now,
        runner=runner,
    )
    print(pub_result.pr_url)


def main(
    argv: list[str] | None = None,
    *,
    llm: LlmClient | None = None,
    runner: CommandRunner | None = None,
    repo_root: Path | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = repo_root if repo_root is not None else Path.cwd()
    run = runner if runner is not None else SubprocessRunner()

    try:
        if args.command == "gate":
            _gate(args.bundle, root)
        elif args.command == "run":
            _run(
                args.bundle,
                no_publish=args.no_publish,
                root=root,
                runner=run,
                llm=llm,
            )
        return 0
    except PipelineError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - catch unexpected errors to avoid leaking secret bodies
        print(type(exc).__name__, file=sys.stderr)
        return 1
