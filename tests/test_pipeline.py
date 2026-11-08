import json
import os

from conftest import edit

from docheal.cli import main
from docheal.config import Settings
from docheal.pipeline import apply_corrections, decide_mode, run_check
from docheal.report import MARKER, render_comment


def settings(repo, **kw):
    return Settings(repo_path=str(repo), base="main", llm_provider="rules", embedding_provider="hashing",
                    write_index=False, **kw)


def test_end_to_end_autofix_and_review(sample_repo):
    edit(sample_repo, "taskq/client.py", "max_retries: int = 3", "max_retries: int = 5")
    edit(sample_repo, "taskq/api.py", '@app.delete("/tasks/{task_id}")\ndef cancel_task(task_id: str) -> dict:\n'
         '    """Cancel a queued task."""\n    return {"id": task_id, "state": "cancelled"}\n', "")
    report = run_check(settings(sample_repo))
    modes = {c.doc_id: c.mode for c in report.corrections}
    assert modes == {"docs/client.md#python-client-creating-a-client": "auto_fix",
                     "docs/api.md#rest-api-cancel-a-task": "review"}
    written = apply_corrections(sample_repo, report.corrections)
    assert "max_retries=5" in written["docs/client.md"]
    assert "docs/api.md" not in written  # review items are never auto-applied
    body = render_comment(report, "o/r", "abc123", "https://github.com/o/r/pull/9")
    assert MARKER in body and "1 auto-fixed (see https://github.com/o/r/pull/9), 1 flagged for review" in body
    assert "blob/abc123/docs/api.md#L" in body


def test_threshold_controls_mode():
    assert decide_mode("simple", 0.9, True, 0.8) == "auto_fix"
    assert decide_mode("simple", 0.7, True, 0.8) == "review"
    assert decide_mode("complex", 0.99, True, 0.8) == "review"
    assert decide_mode("simple", 0.99, False, 0.8) == "review"


def test_no_changes_report(sample_repo):
    report = run_check(settings(sample_repo))
    assert report.corrections == [] and "No documentation-relevant" in render_comment(report)


def test_index_command_persists_graph(sample_repo, capsys):
    assert main(["index", "--repo", str(sample_repo)]) == 0
    data = json.loads((sample_repo / ".docheal" / "index.json").read_text())
    assert data["stats"]["links"] > 10 and data["code_chunks"] and data["doc_sections"]


def test_action_entrypoint_without_token(sample_repo, tmp_path, monkeypatch):
    edit(sample_repo, "taskq/config.py", "worker_concurrency: int = 4", "worker_concurrency: int = 8")
    out = tmp_path / "out.txt"
    summary = tmp_path / "summary.md"
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "base": {"sha": "main", "ref": "main"},
                                                  "head": {"sha": "HEAD", "ref": "feature",
                                                           "repo": {"full_name": "o/r"}}}}))
    for k in ("GITHUB_TOKEN", "DOCHEAL_GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("DOCHEAL_REPO_PATH", str(sample_repo))
    monkeypatch.setenv("DOCHEAL_EMBEDDING_PROVIDER", "hashing")
    assert main(["action"]) == 0
    text = out.read_text()
    assert "auto-fixed-count<<EOF_DOCHEAL\n1\n" in text and "stale-count<<EOF_DOCHEAL\n1\n" in text
    assert "Doc Check Results" in summary.read_text()
    monkeypatch.setenv("DOCHEAL_FAIL_ON_STALE", "true")
    edit(sample_repo, "taskq/cli.py", '@cli.command("worker")', '@cli.command("worker2")\n@click.option("--x")')
    assert os.path.exists(sample_repo / ".docheal" / "report.json")
