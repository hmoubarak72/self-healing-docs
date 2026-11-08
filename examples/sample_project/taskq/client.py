"""HTTP client for the taskq server."""

import time

import requests

DEFAULT_PRIORITY = "normal"


class TaskClient:
    """Submit tasks to a taskq server and poll their status."""

    def __init__(self, base_url: str, timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def submit(self, task_name: str, payload: dict, priority: str = "normal") -> str:
        """Submit a task and return its id."""
        body = {"name": task_name, "payload": payload, "priority": priority}
        resp = self._post("/tasks", body)
        return resp["id"]

    def get_status(self, task_id: str) -> dict:
        """Return the status document for ``task_id``."""
        return self._get(f"/tasks/{task_id}")

    def wait(self, task_id: str, poll_interval: float = 1.0) -> dict:
        """Block until the task finishes and return its final status."""
        while True:
            status = self.get_status(task_id)
            if status["state"] in ("done", "failed"):
                return status
            time.sleep(poll_interval)

    def _post(self, path: str, body: dict) -> dict:
        for attempt in range(self.max_retries):
            try:
                r = requests.post(self.base_url + path, json=body, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except requests.RequestException:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        return {}

    def _get(self, path: str) -> dict:
        r = requests.get(self.base_url + path, timeout=self.timeout)
        r.raise_for_status()
        return r.json()
