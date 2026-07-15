# Stock Agent V2 Progress

## Current state

- V2 task lifecycle, Agent runtime, evidence workflows, specialist agents, signal safety boundary, report validation, Web, Worker, CLI, Telegram transport and read-only MCP are implemented.
- The codebase is V2-only at runtime. V1 ReAct, broker, continuous market-watch, formula strategy and notification code have been removed.
- Single-step tests and legacy regression fixtures have been removed. `tests/test_v2_end_to_end.py` is the one retained offline end-to-end verification.
- The immutable baseline SQLite migration remains for checksum and shared audit-table compatibility; it does not retain a V1 runtime path.

## Next review point

M1 in [task_agent_v2.md](task_agent_v2.md): normalize report model evidence IDs to canonical evidence references before final validation. This addresses model serialization drift while preserving strict evidence grounding.
