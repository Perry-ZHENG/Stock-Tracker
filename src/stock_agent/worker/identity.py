"""Worker instance identity helpers."""

from __future__ import annotations

import hashlib
import os
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerIdentity:
    instance_id: str
    host_id: str
    multi_instance_enabled: bool = False

    def lock_owner(self) -> str:
        return f"{self.host_id}:{self.instance_id}"


def build_worker_identity() -> WorkerIdentity:
    host = os.getenv("STOCK_AGENT_HOST_ID") or socket.gethostname() or "localhost"
    configured_instance = os.getenv("STOCK_AGENT_INSTANCE_ID")
    if configured_instance:
        instance_id = configured_instance
    else:
        digest = hashlib.sha1(f"{host}|stock-agent".encode("utf-8")).hexdigest()[:12]
        instance_id = f"local-{digest}"
    multi_instance_enabled = os.getenv("STOCK_AGENT_MULTI_INSTANCE", "false").lower() == "true"
    return WorkerIdentity(instance_id=instance_id, host_id=host, multi_instance_enabled=multi_instance_enabled)


__all__ = ["WorkerIdentity", "build_worker_identity"]
