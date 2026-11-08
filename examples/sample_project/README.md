# taskq

A tiny task queue. Submit work from Python, the CLI, or the REST API.

## Installation

```bash
pip install taskq
```

## Quickstart

```python
from taskq import TaskClient

client = TaskClient("http://localhost:8000")
task_id = client.submit("resize-image", {"path": "cat.png"})
print(client.wait(task_id))
```

## License

MIT
