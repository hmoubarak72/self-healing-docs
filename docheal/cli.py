"""Command-line interface.

    docheal index  [--repo .]                      build + persist the code-to-docs graph
    docheal check  [--repo .] [--base origin/main] [--head WORKTREE] [--apply] [--json]
    docheal action                                 GitHub Action entrypoint (reads env)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import Settings
from .diff import file_at
from .embeddings import get_embedder
from .graph import build_index
from .pipeline import apply_corrections, apply_to_text, group_by_file, run_check
from .report import headline, render_comment, render_fix_pr_body

log = logging.getLogger("docheal")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)


def cmd_index(args: argparse.Namespace) -> int:
    s = Settings.from_env(repo_path=args.repo)
    embedder = get_embedder(s.embedding_provider)
    graph = build_index(s.repo_path, s.code_globs, s.doc_globs, s.exclude, embedder=embedder,
                        threshold=s.similarity_threshold or (0.35 if embedder.name.startswith("hashing") else 0.45),
                        persist_dir=str(Path(s.repo_path) / ".docheal" / "chroma") if args.chroma else None)
    out = graph.save(Path(s.repo_path) / (args.output or s.index_path))
    print(json.dumps({"index": str(out), **graph.stats()}, indent=2))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    s = Settings.from_env(repo_path=args.repo, base=args.base, head=args.head, llm_provider=args.provider,
                          confidence_threshold=args.threshold, write_index=not args.no_index)
    report = run_check(s)
    if args.apply:
        written = apply_corrections(s.repo_path, report.corrections,
                                    ("auto_fix", "review") if args.apply_all else ("auto_fix",))
        for p in written:
            log.info("updated %s", p)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(render_comment(report))
    if s.fail_on_stale and report.flagged:
        return 1
    return 0


def cmd_action(_args: argparse.Namespace) -> int:
    from .github_io import GitHubClient, PRContext, set_outputs, write_step_summary

    ctx = PRContext.from_env()
    s = Settings.from_env()
    if ctx.base_sha:
        s.base = ctx.base_sha
    log.info("DocHeal %s — repo=%s pr=%s base=%s", __version__, ctx.repo, ctx.number, s.base)
    report = run_check(s)

    token = os.environ.get("DOCHEAL_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or os.environ.get("INPUT_GITHUB-TOKEN")
    fix_pr_url = ""
    comment_url = ""
    gh = None
    if token and ctx.repo:
        try:
            gh = GitHubClient(token, ctx.repo)
        except Exception as exc:
            log.warning("GitHub client unavailable: %s", exc)

    can_push = gh is not None and not ctx.is_fork and s.mode != "comment-only" and ctx.head_ref and ctx.head_sha
    if report.auto_fixed and can_push:
        files: dict[str, str] = {}
        for path, items in group_by_file(report.corrections).items():
            original = file_at(s.repo_path, ctx.head_sha, path)
            if original is None:
                original = (Path(s.repo_path) / path).read_text(encoding="utf-8")
            new, n = apply_to_text(original, items)
            if n:
                files[path] = new
        if files:
            try:
                fix_pr_url = gh.open_fix_pr(
                    files=files, source_pr=ctx.number, target_branch=ctx.head_ref, from_sha=ctx.head_sha,
                    title=f"📚 DocHeal: fix {len(report.auto_fixed)} stale doc section(s)"
                          + (f" for #{ctx.number}" if ctx.number else ""),
                    body=render_fix_pr_body(report, ctx.number), auto_merge=s.auto_merge)
                log.info("Fix PR: %s", fix_pr_url)
            except Exception as exc:
                log.warning("Could not open fix PR (%s); fixes will be listed in the comment", exc)
    elif report.auto_fixed and ctx.is_fork:
        log.info("Fork PR: token is read-only, reporting fixes in the comment only")

    body = render_comment(report, ctx.repo, ctx.head_sha, fix_pr_url or None)
    if gh is not None and ctx.number:
        try:
            comment_url = gh.upsert_comment(ctx.number, body)
        except Exception as exc:
            log.warning("Could not post PR comment: %s", exc)
    write_step_summary(body)

    report_path = Path(s.repo_path) / ".docheal" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    set_outputs(
        **{
            "stale-sections": json.dumps([{"doc": v.doc_id, "issues": v.issues} for v in report.stale]),
            "corrections": json.dumps([{"doc": c.doc_id, "mode": c.mode, "confidence": c.confidence}
                                       for c in report.corrections]),
            "stale-count": str(len(report.stale)),
            "auto-fixed-count": str(len(report.auto_fixed)),
            "flagged-count": str(len(report.flagged)),
            "fix-pr-url": fix_pr_url,
            "comment-url": comment_url,
            "report-path": str(report_path),
            "summary": headline(report, fix_pr_url or None),
        }
    )
    print(headline(report, fix_pr_url or None))
    if s.fail_on_stale and report.flagged:
        log.error("Stale documentation requires human review (fail-on-stale=true)")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="docheal", description="Self-healing technical documentation")
    p.add_argument("--version", action="version", version=f"docheal {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="build the code-to-docs link graph")
    pi.add_argument("--repo", default=".")
    pi.add_argument("--output", default=None)
    pi.add_argument("--chroma", action="store_true", help="also persist embeddings to a ChromaDB store")
    pi.set_defaults(func=cmd_index)

    pc = sub.add_parser("check", help="detect (and optionally fix) stale docs for a diff")
    pc.add_argument("--repo", default=".")
    pc.add_argument("--base", default=None, help="base ref (default origin/main)")
    pc.add_argument("--head", default=None, help="head ref, or WORKTREE for uncommitted changes")
    pc.add_argument("--provider", default=None, help="auto | anthropic | openai | rules")
    pc.add_argument("--threshold", type=float, default=None, help="auto-fix confidence threshold")
    pc.add_argument("--apply", action="store_true", help="write high-confidence fixes to disk")
    pc.add_argument("--apply-all", action="store_true", help="with --apply, also write review drafts")
    pc.add_argument("--no-index", action="store_true", help="do not rewrite .docheal/index.json")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=cmd_check)

    pa = sub.add_parser("action", help="GitHub Action entrypoint")
    pa.set_defaults(func=cmd_action)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
