# Configuration

## Server settings

The server is configured with `ServerSettings`:

| Field | Default | Description |
|---|---|---|
| `queue_url` | `redis://localhost:6379/0` | Broker connection string |
| `worker_concurrency` | `4` | Worker processes per node |
| `result_ttl_seconds` | `3600` | How long results are kept |

## Limits

Payloads larger than `MAX_PAYLOAD_BYTES` (1048576 bytes) are rejected.

## Environment variables

Set `TASKQ_LOG_LEVEL` to control log verbosity (default `INFO`).
