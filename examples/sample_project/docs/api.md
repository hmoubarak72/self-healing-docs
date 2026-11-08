# REST API

## Create a task

`POST /tasks` enqueues a task and returns `{"id": ...}`.

## Get task status

`GET /tasks/{task_id}` returns the current state of a task.

## Cancel a task

`DELETE /tasks/{task_id}` cancels a queued task.
