"""Server configuration."""

import os
from dataclasses import dataclass

MAX_PAYLOAD_BYTES = 1048576
LOG_LEVEL = os.getenv("TASKQ_LOG_LEVEL", "INFO")


@dataclass
class ServerSettings:
    """Settings for the taskq server process."""

    queue_url: str = "redis://localhost:6379/0"
    worker_concurrency: int = 4
    result_ttl_seconds: int = 3600
