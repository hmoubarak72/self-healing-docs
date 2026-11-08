"""Accuracy benchmark (Phase 5.2).

Runs every labelled scenario in a throwaway git repo and reports true / false
positives, false negatives, mode accuracy and correction quality.

    python -m benchmarks.run                 # deterministic rule engine
    python -m benchmarks.run --provider anthropic   # needs ANTHROPIC_API_KEY
    python -m benchmarks.run --markdown benchmarks/RESULTS.md
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.scenarios import SCENARIOS, Scenario
from docheal.config import Settings
from docheal.llm import get_engine
from docheal.pipeline import apply_corrections, run_check

SAMPLE = ROOT / "examples" / "sample_project"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def run_scenario(sc: Scenario, provider: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(SAMPLE, repo, ignore=shutil.ignore_patterns(".docheal", "__pycache__"))
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "-c", "user.email=b@x", "-c", "user.name=bench", "add", "-A")
        _git(repo, "-c", "user.email=b@x", "-c", "user.name=bench", "commit", "-qm", "base")
        for path, old, new in sc.edits:
            fp = repo / path
            text = fp.read_text()
            if old not in text:
                raise AssertionError(f"[{sc.name}] edit anchor not found in {path}: {old[:40]!r}")
            fp.write_text(text.replace(old, new, 1))
        settings = Settings(repo_path=str(repo), base="main", head="WORKTREE", llm_provider=provider,
                            embedding_provider="hashing", write_index=False)
        report = run_check(settings, engine=get_engine(provider))
        predicted = {c.doc_id: c.mode for c in report.corrections}
        expected = sc.expected_stale
        tp = sorted(set(predicted) & set(expected))
        fp_ = sorted(set(predicted) - set(expected))
        fn = sorted(set(expected) - set(predicted))
        mode_ok = sum(1 for d in tp if predicted[d] == expected[d])
        apply_corrections(repo, report.corrections)
        quality_checks, quality_pass, failures = 0, 0, []
        auto_expected = any(m == "auto_fix" for m in expected.values())
        if auto_expected:
            for f, needles in sc.must_contain.items():
                txt = (repo / f).read_text()
                for n in needles:
                    quality_checks += 1
                    if n in txt:
                        quality_pass += 1
                    else:
                        failures.append(f"{f}: missing {n!r}")
            for f, needles in sc.must_not_contain.items():
                txt = (repo / f).read_text()
                for n in needles:
                    quality_checks += 1
                    if n not in txt:
                        quality_pass += 1
                    else:
                        failures.append(f"{f}: still contains {n!r}")
        return {
            "scenario": sc.name, "category": sc.category, "tp": tp, "fp": fp_, "fn": fn,
            "mode_correct": mode_ok, "quality_checks": quality_checks, "quality_pass": quality_pass,
            "failures": failures, "suspects": len(report.suspects),
            "skipped_changes": len(report.diff.skipped),
            "predicted": predicted,
        }


def summarize(results: list[dict]) -> dict:
    tp = sum(len(r["tp"]) for r in results)
    fp = sum(len(r["fp"]) for r in results)
    fn = sum(len(r["fn"]) for r in results)
    mode = sum(r["mode_correct"] for r in results)
    qc = sum(r["quality_checks"] for r in results)
    qp = sum(r["quality_pass"] for r in results)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "scenarios": len(results), "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "mode_accuracy": round(mode / tp, 3) if tp else 0.0,
        "correction_quality": round(qp / qc, 3) if qc else 0.0,
        "suspects_checked": sum(r["suspects"] for r in results),
    }


def to_markdown(results: list[dict], summary: dict, engine: str) -> str:
    lines = [f"# DocHeal benchmark — engine `{engine}`", "",
             f"{summary['scenarios']} labelled scenarios against `examples/sample_project`.", "",
             "| Metric | Value |", "|---|---|"]
    for k in ("true_positives", "false_positives", "false_negatives", "precision", "recall", "f1",
              "mode_accuracy", "correction_quality", "suspects_checked"):
        lines.append(f"| {k.replace('_', ' ')} | {summary[k]} |")
    lines += ["", "| Scenario | Category | TP | FP | FN | Mode ok | Fix checks |", "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['scenario']} | {r['category']} | {len(r['tp'])} | {len(r['fp'])} | {len(r['fn'])} | "
                     f"{r['mode_correct']}/{len(r['tp'])} | {r['quality_pass']}/{r['quality_checks']} |")
    fails = [(r["scenario"], f) for r in results for f in r["failures"]] + \
            [(r["scenario"], f"FP {d}") for r in results for d in r["fp"]] + \
            [(r["scenario"], f"FN {d}") for r in results for d in r["fn"]]
    if fails:
        lines += ["", "## Misses", ""] + [f"- **{s}** — {f}" for s, f in fails]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="rules")
    ap.add_argument("--json", default=None)
    ap.add_argument("--markdown", default=None)
    ap.add_argument("--min-f1", type=float, default=0.0, help="exit non-zero below this F1 (CI gate)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    engine_name = get_engine(args.provider).name
    results = [run_scenario(sc, args.provider) for sc in SCENARIOS]
    summary = summarize(results)
    md = to_markdown(results, summary, engine_name)
    print(md)
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    if args.markdown:
        Path(args.markdown).write_text(md)
    return 0 if summary["f1"] >= args.min_f1 else 1


if __name__ == "__main__":
    sys.exit(main())
