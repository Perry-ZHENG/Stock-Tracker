# Stock Agent Deployment Examples

This document provides non-production deployment templates for running the
`stock-agent worker` process under macOS `launchd`, Linux `systemd`, or `pm2`.

The templates intentionally do not hardcode a local machine path. Render or edit
the placeholders before installing them on a host.

## Common Environment

Set these values per machine:

- `STOCK_AGENT_BIN`: executable path or command, for example `stock-agent`.
- `STOCK_AGENT_WORKDIR`: project root that contains `configs/`, `data/`, and `src/`.
- `STOCK_AGENT_CONFIG`: config path, usually `configs/config.yaml`.
- `STOCK_AGENT_INTERVAL_SEC`: worker loop interval, usually `30`.
- `STOCK_AGENT_LOG_DIR`: directory for process logs.
- `MARKET_DATA_API_KEY`: optional live market data key.
- `TELEGRAM_BOT_TOKEN`: optional Telegram bot token.
- `NEWS_API_KEY`: optional on-demand news provider key.

The CLI reads `STOCK_AGENT_WORKDIR` and falls back to the current working
directory when it is not set. `stock-agent init-config` and CLI config approval
read `STOCK_AGENT_CONFIG` when choosing the YAML path to write.

Before installing any template, run the offline dry-run validation:

```sh
stock-agent deploy-validate
```

The command loads local config and checks the workdir, storage parents, and demo
CSV path. It does not start the worker, write runtime data, or call the network.

## macOS launchd

Template:

- `deploy/launchd/com.example.stock-agent.worker.plist`

Usage:

1. Copy the plist to a machine-specific path outside the repository.
2. Replace every `${...}` placeholder with the machine's environment values.
3. Run `${STOCK_AGENT_BIN} deploy-validate` from the rendered workdir.
4. Load it with `launchctl bootstrap` for the target user or system domain.
5. Inspect logs at `${STOCK_AGENT_LOG_DIR}/worker.out.log` and
   `${STOCK_AGENT_LOG_DIR}/worker.err.log`.

`launchd` does not expand shell-style placeholders automatically. The plist must
be rendered before installation.

## Linux systemd

Template:

- `deploy/systemd/stock-agent-worker.service`

Usage:

1. Create an environment file referenced by `${STOCK_AGENT_ENV_FILE}`.
2. Replace placeholders in the service file or render it in your deployment
   tooling.
3. Install the rendered service file into the target systemd unit directory.
4. Run `${STOCK_AGENT_BIN} deploy-validate` with the same environment file.
5. Run `systemctl daemon-reload`, then enable and start the service.

Example environment file content:

```sh
STOCK_AGENT_BIN=stock-agent
STOCK_AGENT_WORKDIR=/path/to/stock-agent
STOCK_AGENT_CONFIG=configs/config.yaml
STOCK_AGENT_INTERVAL_SEC=30
MARKET_DATA_API_KEY=
TELEGRAM_BOT_TOKEN=
NEWS_API_KEY=
```

The sample path above is illustrative and must be replaced for the target host.

## pm2

Template:

- `deploy/pm2/ecosystem.config.cjs`

Usage:

```sh
STOCK_AGENT_BIN=stock-agent \
STOCK_AGENT_WORKDIR=/path/to/stock-agent \
STOCK_AGENT_CONFIG=configs/config.yaml \
STOCK_AGENT_INTERVAL_SEC=30 \
stock-agent deploy-validate
STOCK_AGENT_BIN=stock-agent \
STOCK_AGENT_WORKDIR=/path/to/stock-agent \
STOCK_AGENT_CONFIG=configs/config.yaml \
STOCK_AGENT_INTERVAL_SEC=30 \
pm2 start deploy/pm2/ecosystem.config.cjs
```

Use pm2 only for quick experiments or development-style long-running tests. For
stable host deployment, prefer launchd on macOS and systemd on Linux.

## Smoke Checks

After starting the worker:

```sh
stock-agent health
stock-agent cli signals --limit 5
```

If Telegram or live data keys are not configured, the system should still run in
demo/local mode and report optional channels as disabled or unavailable rather
than crashing.
