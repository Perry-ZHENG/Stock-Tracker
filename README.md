# Stock Agent

Local-first US market watch and signal assistant. It can run from demo CSV data,
build 30-minute bars, calculate strategy signals, keep audit traces, and expose
CLI/Telegram-style query flows without requiring live API keys.

## Requirements

- Python 3.12 or newer
- `uv` for development, or `pipx`/`uv tool` for command-style installs

## Quick Start

```sh
uv sync --extra dev
uv run stock-agent init-config
uv run stock-agent run-demo
uv run stock-agent health --verbose
```

The default config uses `data/sample/sample_bars.csv`, so the demo path works
without market data, Telegram, news, or LLM credentials.

## Install As A Tool

From a checked-out repository:

```sh
pipx install .
stock-agent init-config
stock-agent run-demo
```

Or with uv:

```sh
uv tool install .
stock-agent init-config
stock-agent run-demo
```

For editable development, prefer:

```sh
uv sync --extra dev
uv run stock-agent --help
```

## Common Commands

```sh
stock-agent init-config
stock-agent run-demo
stock-agent worker --once
stock-agent cli signals --limit 5
stock-agent retention
stock-agent deploy-validate
```

`stock-agent retention` is dry-run by default. It only applies retention actions
when explicitly called with `--execute`.

## Testing

```sh
uv run --extra dev pytest
```

The test suite is designed to run without live network services. Optional live
providers and notification channels must degrade to local/demo behavior when
credentials are absent.

## Deployment Dry Run

Before installing any service template, render the placeholders for the target
host and run:

```sh
stock-agent deploy-validate
```

Deployment examples live under `deploy/` and are documented in
`docs/deployment.md`. The validation command is offline: it checks local config,
working directory, storage parents, and demo CSV availability without starting
the worker or contacting external APIs.
