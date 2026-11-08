# Command line

## Submitting from the shell

```bash
taskq submit resize-image --priority high --wait
```

`taskq submit NAME` enqueues a task. Use `--priority` to set its priority and `--wait`
to block until it completes.

## Running workers

`taskq worker --concurrency 8` starts a pool of worker processes (default 4).
