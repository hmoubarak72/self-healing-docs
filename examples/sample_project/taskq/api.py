"""REST API for the taskq server."""

from fastapi import FastAPI

app = FastAPI(title="taskq")


@app.post("/tasks")
def create_task(task: dict) -> dict:
    """Enqueue a task. Returns ``{"id": ...}``."""
    return {"id": "t-1"}


@app.get("/tasks/{task_id}")
def read_task(task_id: str) -> dict:
    """Fetch the status of one task."""
    return {"id": task_id, "state": "queued"}


@app.delete("/tasks/{task_id}")
def cancel_task(task_id: str) -> dict:
    """Cancel a queued task."""
    return {"id": task_id, "state": "cancelled"}
