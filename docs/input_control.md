# V2 Input Control

Web, CLI and Telegram may submit or control research tasks, but only one input interface holds submission ownership at a time. This prevents duplicate user actions while the Worker continues independently.

```text
GET  /api/v2/input
POST /api/v2/input/heartbeat
POST /api/v2/input/switch/requests
POST /api/v2/input/switch/requests/{request_id}/approve
POST /api/v2/input/switch/requests/{request_id}/reject
```

The input gate controls only user-originated task changes: submit, provide input, pause, resume, cancel, report retry and signal approval. Status, report retrieval, diagnostics and background worker execution remain available.
