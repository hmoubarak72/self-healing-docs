"""Labelled code-change scenarios against ``examples/sample_project``.

Each scenario applies text edits to a pristine copy of the sample project and
lists the documentation sections that SHOULD be reported stale (ground truth),
the expected handling mode, and checks on the corrected docs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

C = "docs/client.md#python-client-"
CFG = "docs/configuration.md#configuration-"
API = "docs/api.md#rest-api-"
CLI = "docs/cli.md#command-line-"
README_QS = "README.md#taskq-quickstart"


@dataclass
class Scenario:
    name: str
    category: str
    edits: list[tuple[str, str, str]]  # (file, old, new)
    expected_stale: dict[str, str] = field(default_factory=dict)  # doc_id -> expected mode
    must_contain: dict[str, list[str]] = field(default_factory=dict)  # file -> strings after auto-fix
    must_not_contain: dict[str, list[str]] = field(default_factory=dict)


SCENARIOS: list[Scenario] = [
    # ------------------------------------------------------------- simple, auto-fixable
    Scenario(
        "rename-parameter", "signature",
        [("taskq/client.py", "timeout: int = 30,", "request_timeout: int = 30,"),
         ("taskq/client.py", "self.timeout = timeout", "self.timeout = request_timeout")],
        {C + "creating-a-client": "auto_fix"},
        must_contain={"docs/client.md": ["request_timeout=30", "`request_timeout` seconds"]},
        must_not_contain={"docs/client.md": ["(base_url, timeout=30"]},
    ),
    Scenario(
        "change-default-value", "signature",
        [("taskq/client.py", "max_retries: int = 3", "max_retries: int = 5")],
        {C + "creating-a-client": "auto_fix"},
        must_contain={"docs/client.md": ["max_retries=5"]},
    ),
    Scenario(
        "rename-method", "signature",
        [("taskq/client.py", "def get_status(", "def fetch_status("),
         ("taskq/client.py", "status = self.get_status(task_id)", "status = self.fetch_status(task_id)")],
        {C + "checking-status": "auto_fix"},
        must_contain={"docs/client.md": ["`fetch_status(task_id)`"]},
    ),
    Scenario(
        "config-field-default", "config",
        [("taskq/config.py", "worker_concurrency: int = 4", "worker_concurrency: int = 8")],
        {CFG + "server-settings": "auto_fix"},
        must_contain={"docs/configuration.md": ["| `worker_concurrency` | `8` |"]},
    ),
    Scenario(
        "module-constant", "config",
        [("taskq/config.py", "MAX_PAYLOAD_BYTES = 1048576", "MAX_PAYLOAD_BYTES = 2097152")],
        {CFG + "limits": "auto_fix"},
        must_contain={"docs/configuration.md": ["(2097152 bytes)"]},
    ),
    Scenario(
        "env-var-default", "config",
        [("taskq/config.py", 'os.getenv("TASKQ_LOG_LEVEL", "INFO")', 'os.getenv("TASKQ_LOG_LEVEL", "WARNING")')],
        {CFG + "environment-variables": "auto_fix"},
        must_contain={"docs/configuration.md": ["(default `WARNING`)"]},
    ),
    Scenario(
        "endpoint-route", "api",
        [("taskq/api.py", '@app.post("/tasks")', '@app.post("/jobs")')],
        {API + "create-a-task": "auto_fix"},
        must_contain={"docs/api.md": ["`POST /jobs`", "`GET /tasks/{task_id}`"]},
    ),
    Scenario(
        "cli-option-rename", "cli",
        [("taskq/cli.py", '"--wait", is_flag=True', '"--block", is_flag=True'),
         ("taskq/cli.py", "def submit_cmd(name, priority, wait):", "def submit_cmd(name, priority, block):"),
         ("taskq/cli.py", "    if wait:", "    if block:")],
        {CLI + "submitting-from-the-shell": "auto_fix"},
        must_contain={"docs/cli.md": ["--priority high --block", "`--block`"], "README.md": ["client.wait(task_id)"]},
    ),
    Scenario(
        "cli-command-rename", "cli",
        [("taskq/cli.py", '@cli.command("worker")', '@cli.command("run-worker")')],
        {CLI + "running-workers": "auto_fix"},
        must_contain={"docs/cli.md": ["`taskq run-worker --concurrency 8`", "pool of worker processes"]},
    ),
    Scenario(
        "default-poll-interval", "signature",
        [("taskq/client.py", "poll_interval: float = 1.0", "poll_interval: float = 0.5")],
        {C + "checking-status": "auto_fix"},
        must_contain={"docs/client.md": ["poll_interval=0.5"]},
    ),
    Scenario(
        "rename-class", "signature",
        [("taskq/client.py", "class TaskClient:", "class QueueClient:"),
         ("taskq/__init__.py", "from .client import TaskClient", "from .client import QueueClient"),
         ("taskq/__init__.py", '__all__ = ["TaskClient"]', '__all__ = ["QueueClient"]'),
         ("taskq/cli.py", "from .client import TaskClient", "from .client import QueueClient"),
         ("taskq/cli.py", 'client = TaskClient("http://localhost:8000")', 'client = QueueClient("http://localhost:8000")')],
        {C + "creating-a-client": "auto_fix", README_QS: "auto_fix"},
        must_contain={"README.md": ["from taskq import QueueClient"], "docs/client.md": ["`QueueClient(base_url"]},
    ),
    # ------------------------------------------------------------- complex, needs review
    Scenario(
        "remove-endpoint", "api",
        [("taskq/api.py", '''@app.delete("/tasks/{task_id}")
def cancel_task(task_id: str) -> dict:
    """Cancel a queued task."""
    return {"id": task_id, "state": "cancelled"}
''', "")],
        {API + "cancel-a-task": "review"},
    ),
    Scenario(
        "new-required-parameter", "signature",
        [("taskq/client.py", "def submit(self, task_name: str, payload: dict, priority",
          "def submit(self, task_name: str, payload: dict, queue: str, priority")],
        {C + "submitting-tasks": "review", README_QS: "review"},
    ),
    Scenario(
        "remove-cli-command", "cli",
        [("taskq/cli.py", '''@cli.command("worker")
@click.option("--concurrency", default=4, help="Number of worker processes.")
def worker_cmd(concurrency):
    """Start a worker pool."""
    click.echo(f"starting {concurrency} workers")
''', "")],
        {CLI + "running-workers": "review"},
    ),
    # ------------------------------------------------------------- negatives (must NOT flag)
    Scenario(
        "comment-only", "negative",
        [("taskq/client.py", "        body = {", "        # build the request body\n        body = {"),
         ("taskq/config.py", "MAX_PAYLOAD_BYTES = 1048576", "MAX_PAYLOAD_BYTES = 1048576  # 1 MiB")],
    ),
    Scenario(
        "whitespace-only", "negative",
        [("taskq/api.py", 'return {"id": "t-1"}', 'return {"id":   "t-1"}')],
    ),
    Scenario(
        "docstring-only", "negative",
        [("taskq/client.py", '"""Submit a task and return its id."""', '"""Submit a task; returns the new task id."""')],
    ),
    Scenario(
        "test-file-only", "negative",
        [("tests/test_client.py", 'TaskClient("http://x/")', 'TaskClient("http://y/")')],
    ),
    Scenario(
        "internal-refactor", "negative",
        [("taskq/client.py", "time.sleep(2 ** attempt)", "time.sleep(min(2 ** attempt, 10))"),
         ("taskq/client.py", '''        body = {"name": task_name, "payload": payload, "priority": priority}
        resp = self._post("/tasks", body)''', '''        request = {"name": task_name, "payload": payload, "priority": priority}
        resp = self._post("/tasks", request)''')],
    ),
    Scenario(
        "new-optional-parameter", "negative",
        [("taskq/client.py", 'priority: str = "normal") -> str:', 'priority: str = "normal", tags: list | None = None) -> str:')],
    ),
    Scenario(
        "new-function", "negative",
        [("taskq/api.py", "@app.delete(", '''@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"ok": True}


@app.delete(''')],
    ),
]
