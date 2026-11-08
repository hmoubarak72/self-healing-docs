from conftest import edit, git

from docheal.diff import detect_changes


def types(res):
    return {c.chunk_id.split("::")[1]: c.change_type for c in res.changes}


def test_cosmetic_docstring_and_tests_are_filtered(sample_repo):
    edit(sample_repo, "taskq/client.py", "        body = {", "        # comment\n        body = {")
    edit(sample_repo, "taskq/client.py", '"""Submit a task and return its id."""', '"""Submit; return id."""')
    edit(sample_repo, "tests/test_client.py", "http://x/", "http://z/")
    res = detect_changes(sample_repo, "main")
    assert res.changes == []
    assert {c.change_type for c in res.skipped} <= {"docstring_only", "cosmetic"}
    assert "tests/test_client.py" in res.skipped_files


def test_signature_rename_and_removal(sample_repo):
    edit(sample_repo, "taskq/client.py", "max_retries: int = 3", "max_retries: int = 5")
    edit(sample_repo, "taskq/client.py", "def get_status(", "def fetch_status(")
    edit(sample_repo, "taskq/api.py", '@app.delete("/tasks/{task_id}")\ndef cancel_task', '\ndef _cancel_task')
    t = types(detect_changes(sample_repo, "main"))
    assert t["TaskClient.__init__"] == "signature_changed"
    assert t["TaskClient.get_status"] == "signature_changed"  # paired rename keeps the old id
    assert t["cancel_task"] == "removed"


def test_committed_head_range(sample_repo):
    git(sample_repo, "checkout", "-qb", "feature")
    edit(sample_repo, "taskq/config.py", "worker_concurrency: int = 4", "worker_concurrency: int = 8")
    git(sample_repo, "commit", "-qam", "change")
    res = detect_changes(sample_repo, "main", "feature")
    assert types(res) == {"ServerSettings": "signature_changed"}
