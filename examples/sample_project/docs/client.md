# Python client

## Creating a client

`TaskClient(base_url, timeout=30, max_retries=3)` creates a client bound to one server.
Requests time out after `timeout` seconds, and failed submissions are retried up to
`max_retries` times with exponential backoff.

## Submitting tasks

Call `submit(task_name, payload, priority="normal")` to enqueue a task. It returns the task id.

```python
task_id = client.submit("send-email", {"to": "a@b.c"}, priority="high")
```

## Checking status

`get_status(task_id)` returns the status document. To block until a task finishes, use
`wait(task_id, poll_interval=1.0)`, which polls every `poll_interval` seconds.
