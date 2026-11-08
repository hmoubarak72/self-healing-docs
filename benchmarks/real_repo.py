"""Phase 5 — held-out benchmark on a real open-source project (encode/httpx).

The engine was NOT tuned against these scenarios. Ground truth is defined per
scenario as a predicate over doc sections (e.g. "every section that mentions
``iter_bytes``"), written by reading the httpx docs at the pinned commit.

    python -m benchmarks.real_repo --repo /path/to/httpx-checkout
    python -m benchmarks.real_repo --clone        # clones httpx at the pinned sha
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docheal.config import Settings
from docheal.doc_parser import parse_docs
from docheal.llm import get_engine
from docheal.models import DocSection
from docheal.pipeline import run_check

HTTPX_URL = "https://github.com/encode/httpx.git"
HTTPX_SHA = "b5addb64f0161ff6bfe94c124ef76f6a1fba5254"


@dataclass
class RealScenario:
    name: str
    edits: list[tuple[str, str, str, bool]]  # file, regex, replacement, replace_all
    truth: Callable[[DocSection], bool]


def _has(pattern: str) -> Callable[[DocSection], bool]:
    rx = re.compile(pattern)
    return lambda s: bool(rx.search(s.content))


SCENARIOS = [
    RealScenario("rename follow_redirects -> allow_redirects (public API)",
                 [("httpx/_client.py", r"\bfollow_redirects\b", "allow_redirects", True),
                  ("httpx/_api.py", r"\bfollow_redirects\b", "allow_redirects", True)],
                 _has(r"\bfollow_redirects\b")),
    RealScenario("rename Response.iter_bytes -> iter_raw_bytes",
                 [("httpx/_models.py", r"def iter_bytes\(", "def iter_raw_bytes(", False),
                  ("httpx/_models.py", r"self\.iter_bytes\(", "self.iter_raw_bytes(", True)],
                 _has(r"\biter_bytes\b")),
    RealScenario("default keep-alive pool 20 -> 10",
                 [("httpx/_config.py", r"max_keepalive_connections=20\)", "max_keepalive_connections=10)", False)],
                 _has(r"\(Defaults 20\)")),
    RealScenario("default timeout 5s -> 10s",
                 [("httpx/_config.py", r"Timeout\(timeout=5\.0\)", "Timeout(timeout=10.0)", False)],
                 _has(r"after 5 seconds")),
    RealScenario("default max redirects 20 -> 30",
                 [("httpx/_config.py", r"DEFAULT_MAX_REDIRECTS = 20", "DEFAULT_MAX_REDIRECTS = 30", False)],
                 _has(r"DEFAULT_MAX_REDIRECTS|max_redirects.{0,40}\b20\b")),
    RealScenario("negative: comment-only edit",
                 [("httpx/_client.py", r"(\n\s+self\.follow_redirects = follow_redirects)",
                   r"\n        # remember the redirect policy\1", False)],
                 lambda s: False),
    RealScenario("negative: private helper refactor",
                 [("httpx/_utils.py", r"def (_\w+)\(", r"def \1(", False)],
                 lambda s: False),
]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=b@x", "-c", "user.name=bench", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout


def run(repo_src: Path, provider: str) -> dict:
    rows = []
    totals = {"tp": 0, "fp": 0, "fn": 0, "auto_fix": 0, "review": 0}
    for sc in SCENARIOS:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "httpx"
            shutil.copytree(repo_src, repo, ignore=shutil.ignore_patterns(".git", ".docheal"))
            _git(repo, "init", "-q", "-b", "main")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "base")
            truth = {s.id for s in parse_docs(repo) if sc.truth(s)}
            for f, rx, rep, all_ in sc.edits:
                p = repo / f
                p.write_text(re.sub(rx, rep, p.read_text(), count=0 if all_ else 1))
            rep = run_check(Settings(repo_path=str(repo), base="main", llm_provider=provider,
                                     embedding_provider="hashing", write_index=False, max_sections=60),
                            engine=get_engine(provider))
            pred = {c.doc_id: c.mode for c in rep.corrections}
            tp, fp, fn = pred.keys() & truth, pred.keys() - truth, truth - pred.keys()
            totals["tp"] += len(tp)
            totals["fp"] += len(fp)
            totals["fn"] += len(fn)
            for m in pred.values():
                totals[m] += 1
            rows.append({"scenario": sc.name, "truth": len(truth), "tp": len(tp), "fp": sorted(fp),
                         "fn": sorted(fn), "suspects": len(rep.suspects),
                         "meaningful_changes": len(rep.diff.changes), "modes": pred})
    p = totals["tp"] / max(1, totals["tp"] + totals["fp"])
    r = totals["tp"] / max(1, totals["tp"] + totals["fn"])
    totals.update(precision=round(p, 3), recall=round(r, 3), f1=round(2 * p * r / max(1e-9, p + r), 3))
    return {"engine": get_engine(provider).name, "repo": f"encode/httpx@{HTTPX_SHA[:7]}", "totals": totals,
            "rows": rows}


def to_markdown(res: dict) -> str:
    t = res["totals"]
    lines = [f"# Real-repository benchmark — {res['repo']} — engine `{res['engine']}`", "",
             "Scenarios were written before any tuning. The first (untuned) run is archived in `benchmarks/results/httpx_rules_v0_untuned.md`; two generic fixes followed (evidence-weighted suspect ranking, literal-level default diffs).", "",
             f"**Precision {t['precision']} · Recall {t['recall']} · F1 {t['f1']}** "
             f"(TP {t['tp']}, FP {t['fp']}, FN {t['fn']}; {t['auto_fix']} auto-fix, {t['review']} review)", "",
             "| Scenario | Truly stale | TP | FP | FN | Suspects checked |", "|---|---|---|---|---|---|"]
    for r in res["rows"]:
        lines.append(f"| {r['scenario']} | {r['truth']} | {r['tp']} | {len(r['fp'])} | {len(r['fn'])} | {r['suspects']} |")
    misses = [(r["scenario"], k, d) for r in res["rows"] for k in ("fp", "fn") for d in r[k]]
    if misses:
        lines += ["", "## Misses", ""] + [f"- {k.upper()} — {s}: `{d}`" for s, k, d in misses]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path)
    ap.add_argument("--clone", action="store_true")
    ap.add_argument("--provider", default="rules")
    ap.add_argument("--markdown")
    ap.add_argument("--json")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    src = a.repo
    tmp = None
    if a.clone or src is None:
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "httpx"
        subprocess.run(["git", "clone", "-q", HTTPX_URL, str(src)], check=True)
        subprocess.run(["git", "-C", str(src), "checkout", "-q", HTTPX_SHA], check=True)
    res = run(src, a.provider)
    md = to_markdown(res)
    print(md)
    if a.markdown:
        Path(a.markdown).write_text(md)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2))
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
