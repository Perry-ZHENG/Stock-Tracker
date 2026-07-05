# Stock Agent Task Breakdown

## 1. 文档目的

本文档基于 `PRD.md` 和 `design.md` 拆解 Stock Agent 首版开发任务，目标是让开发 Agent 可以按任务顺序实现、验证和回归。

首版原则：
- 可启动：`stock-agent init-config`、`stock-agent run-demo`、`stock-agent health` 能端到端运行。
- 可配置：默认 `configs/config.yaml` 可生成、可校验、可审核、可 reload。
- 可存储：核心状态写入 SQLite，可重放数据写入文件层。
- 可测试：不依赖网络、Telegram、LLM、市场开盘时间。
- 可回归：固定 `data/sample/sample_bars.csv` 输入生成确定性 expected signal。

首版非目标：
- 不接入券商。
- 不下单。
- 不做持仓、PnL、收益回测。
- 不用 LLM 计算指标、生成信号或做监督复算。
- 不做每日新闻晨报，新闻仅按需查询。

## 2. 任务状态约定

状态：
- `todo`：尚未开始。
- `in_progress`：正在实现。
- `blocked`：被外部条件阻塞。
- `done`：已实现并通过验收。

优先级：
- `P0`：首版端到端必须完成。
- `P1`：首版建议完成，会显著提升可用性。
- `P2`：后续增强，不阻塞 v1。
- `P3`：进阶盯盘闭环，目标是让 worker 真正执行实时/准实时行情、策略、监督、通知链路。
- `P4`：自然语言与双入口交互增强，目标是让 CLI/Telegram 可用自然语言查询和发起受控变更。
- `P5`：券商或交易相关 API 安全接入，默认只读或行情用途，禁止自动下单。
- `P6`：监督 Agent、可观测性和生产安全增强。
- `P7`：多机器、长期运行、运维和发布增强。

任务完成要求：
- 有代码实现。
- 有测试或明确的手动验收命令。
- 不引入真实密钥。
- 不破坏 `run-demo` 离线运行。
- 更新必要文档。

## 3. 里程碑

### M0：项目骨架与配置

目标：项目可以安装、启动 CLI，并生成默认配置。

完成标志：
- `stock-agent init-config` 可执行。
- 自动生成 `configs/config.yaml` 和 `.env.example`。
- 配置 schema 校验可运行。

### M1：离线 Demo 闭环

目标：无网络、无 key、无 Telegram token 时跑通 demo。

完成标志：
- `stock-agent run-demo` 读取 `data/sample/sample_bars.csv`。
- 生成 `ma_cross_demo_2_3` expected signal。
- 写入 SQLite 中的 `signals`、`trace_chain`、`health_metrics`。
- `stock-agent health` 可查询健康状态。

### M2：正式策略骨架

目标：实现正式 `ma_cross` 与 `boll` 规则骨架，保证 warm-up 和无信号行为正确。

完成标志：
- 正式 `ma_cross` 支持 `MA3/MA5`、`MA5/MA10`、`MA10/MA20`。
- `boll` 支持带宽、开口、缩口规则。
- 历史窗口不足时不产信号。

### M3：交互与审核

目标：CLI 查询、配置审核状态机、Telegram listener skeleton 可用。

完成标志：
- CLI 可查询最近信号、健康状态、配置变更。
- Telegram 可发起配置修改请求，但不能直接改状态。
- CLI approve 后才写 YAML 并 reload。

### M4：后台 Worker 与按需新闻

目标：Worker 调度骨架、健康上报、按需新闻查询模块可用。

完成标志：
- `stock-agent worker` 可启动后台循环。
- 新闻模块只在 CLI/Telegram 明确请求时运行。
- 新闻不阻塞盯盘和策略计算。

### M5：实时盯盘闭环

目标：worker 不再只是 heartbeat skeleton，而是能按配置加载数据、构造 bar、执行策略、监督校验、持久化 signal 并通知用户。

完成标志：
- `stock-agent worker --once` 可在 demo/live provider 下执行一轮完整 pipeline。
- worker 可按 `configs/config.yaml` 的 symbol、provider、bar、strategy 配置运行。
- MA、BOLL、MACD、KDJ、Active J 等已启用策略能由统一 Strategy Engine 调用。
- 多策略同一标的同一 timestamp 触发时，通知可合并展示，SQLite raw signal 仍保持独立。
- 每条 signal 均有 trace、source_bar_ids、data_quality 和 supervisor 结果。

### M6：自然语言双入口

目标：CLI 和 Telegram 都能接收自然语言查询；状态变更保持 CLI 审核边界，Telegram 不能直接执行。

完成标志：
- CLI 和 Telegram 共用同一套 Command Intent schema。
- 自然语言解析只用于“把用户话语转成受控命令”，不得直接修改配置、计算指标或生成信号。
- 查询类意图可直接执行，变更类意图进入 pending_review。
- LLM 未配置时仍支持结构化命令。

### M7：安全券商 API 接入

目标：为未来接入券商 API 做安全边界和只读能力，允许使用券商行情或账户只读信息，但禁止自动下单。

完成标志：
- broker adapter 只暴露 read-only market data/account snapshot 接口。
- 任何 order、transfer、credential mutation 接口都默认不存在或被硬拦截。
- 如未来需要交易动作，必须另起 PRD 和任务，不允许在本任务链内偷偷实现。
- API key、account id、token 不入库、不入日志、不入 trace。

### M8：监督 Agent 与质量防线

目标：监督 Agent 从 v1 schema 校验升级为独立复算、异常隔离、提示文案安全审查。

完成标志：
- 可用独立实现复算 MA/BOLL/MACD/KDJ/Active J 关键指标。
- 异常 bar、主备数据偏差、指标复算不一致时抑制普通提醒。
- 外发文案不得把观察信号说成确定性收益或下单建议。
- supervisor 拦截结果可被 CLI/Telegram 查询。

### M9：生产化运行

目标：面向长时间运行和多环境部署，补齐恢复、观测、通知可靠性、数据保留和升级流程。

完成标志：
- 启动时自动检测缺口并可补拉、重建 30m bar、重算相关 signal。
- 通知 outbox 可重放，避免进程崩溃导致提醒丢失。
- 健康指标覆盖数据源、策略、通知、supervisor、配置审核积压。
- 多机器运行有明确实例身份和锁策略，不产生重复提醒。

## 4. P0 任务拆解

### T-001：初始化 Python 项目结构

状态：`done`

优先级：`P0`

依赖：无

任务内容：
- 创建 `pyproject.toml`。
- 创建 `src/stock_agent/` 包结构。
- 创建 `tests/` 目录。
- 配置 `stock-agent` CLI entry point。

产出：
- `pyproject.toml`
- `src/stock_agent/__init__.py`
- `src/stock_agent/cli.py`
- 基础测试目录

验收标准：
- 本地可以安装项目。
- `stock-agent --help` 可运行。
- 不依赖外部网络或真实密钥。

### T-002：实现 CLI 命令骨架

状态：`done`

优先级：`P0`

依赖：T-001

任务内容：
- 实现命令组 `stock-agent`。
- 支持 `init-config`、`run-demo`、`cli`、`telegram`、`worker`、`health`。
- 未实现的命令应给出清晰提示，不应静默失败。

产出：
- `src/stock_agent/cli.py`
- CLI smoke tests

验收标准：
- `stock-agent init-config --help` 可运行。
- `stock-agent run-demo --help` 可运行。
- `stock-agent health --help` 可运行。

### T-003：配置文件生成与校验

状态：`done`

优先级：`P0`

依赖：T-001、T-002

任务内容：
- 实现默认 `configs/config.yaml` 生成。
- 实现 `.env.example` 生成。
- 使用 Pydantic 或等价方案校验配置。
- 配置包含 `app`、`provider`、`symbols`、`bar`、`strategies`、`telegram`、`news`、`llm`、`storage`、`health`。

产出：
- `src/stock_agent/config.py`
- `configs/config.yaml`
- `.env.example`
- `tests/test_config.py`

验收标准：
- `stock-agent init-config` 在文件不存在时创建默认配置。
- 重复执行不覆盖用户已有配置，除非显式 force。
- 无真实密钥写入仓库。

### T-004：定义标准 Schema

状态：`done`

优先级：`P0`

依赖：T-001

任务内容：
- 定义 `bar`、`signal`、`trace_chain`、`strategy_snapshot`、`news_item`、`health_metric` schema。
- `signal` 统一字段必须为：`signal_id`、`strategy_id`、`symbol`、`timestamp`、`direction`、`strength`、`confidence`、`reason`、`trace_id`、`source_bar_ids`、`data_quality`、`created_at`。
- 时间字段内部统一 UTC。

产出：
- `src/stock_agent/schemas.py` 或 `src/stock_agent/models/`
- schema validation tests

验收标准：
- 缺少必填字段时校验失败。
- `direction` 只允许 `buy_watch`、`sell_watch`、`observe`。
- schema 不包含旧字段名 `strategy`、`side`、`signal_strength`、`evidence`。

### T-005：SQLite 初始化与 Repository 层

状态：`done`

优先级：`P0`

依赖：T-003、T-004

任务内容：
- 初始化 SQLite 数据库。
- 创建表：`signals`、`trace_chain`、`health_metrics`、`checkpoints`、`config_changes`、`notifications`、`news_items`。
- 提供 repository 函数，业务逻辑不得直接散落 SQL。

产出：
- `src/stock_agent/storage/sqlite.py`
- `src/stock_agent/storage/repositories.py`
- SQLite migration/init tests

验收标准：
- demo 模式自动创建 `data/runtime/stock_agent.sqlite`。
- 可插入并查询 `signals`、`trace_chain`、`health_metrics`。
- 测试不依赖外部数据库。

### T-006：准备固定 sample CSV 与 expected signal

状态：`done`

优先级：`P0`

依赖：T-004

任务内容：
- 创建 `data/sample/sample_bars.csv`。
- 创建 `tests/fixtures/expected_signals/ma_cross_demo_2_3.json`。
- sample CSV 使用 PRD 中 5 根 `QQQ 30m` bar。

产出：
- `data/sample/sample_bars.csv`
- `tests/fixtures/expected_signals/ma_cross_demo_2_3.json`

验收标准：
- `run-demo` 和 regression test 使用同一份 `data/sample/sample_bars.csv`。
- 不再创建第二份等价 bar fixture。
- expected signal 时间为 `2026-05-22T15:30:00Z`。

### T-007：CSV Demo Provider

状态：`done`

优先级：`P0`

依赖：T-003、T-004、T-006

任务内容：
- 实现 `csv_demo` provider。
- 从 `provider.csv_demo.path` 读取 CSV。
- 标准化为 `bar` schema。
- 记录 `source=demo_csv`。

产出：
- `src/stock_agent/providers/base.py`
- `src/stock_agent/providers/csv_demo.py`
- provider tests

验收标准：
- 可读取 `data/sample/sample_bars.csv`。
- 字段类型正确。
- CSV 缺字段或格式错误时给出可读错误。

### T-008：Bar 加载与校验

状态：`done`

优先级：`P0`

依赖：T-004、T-007

任务内容：
- 实现标准 bar 校验。
- 实现常规交易时段过滤接口。
- demo 输入已经是 `30m` bar 时直接校验并透传。
- 为未来 tick/1m 聚合预留 `BarBuilder` 接口。

产出：
- `src/stock_agent/bars/builder.py`
- `src/stock_agent/bars/validation.py`
- bar tests

验收标准：
- 异常 bar 不进入策略计算。
- 盘前/盘后数据可标记，但 v1 策略默认不使用。
- bar ID 生成确定性。

### T-009：实现 `ma_cross_demo_2_3`

状态：`done`

优先级：`P0`

依赖：T-004、T-006、T-008

任务内容：
- 实现 demo 策略 `ma_cross_demo_2_3`。
- `MA2 = mean(close[-2:])`。
- `MA3 = mean(close[-3:])`。
- 当上一根 `MA2 <= MA3` 且当前 `MA2 > MA3` 时生成 `buy_watch`。
- 该策略仅用于 regression，不用于真实盯盘。

产出：
- `src/stock_agent/strategies/ma_cross_demo.py`
- strategy unit tests

验收标准：
- 5 根 sample bar 只生成 1 条 expected signal。
- signal 字段完全符合标准 schema。
- `source_bar_ids` 包含参与计算的 3 根 bar。

### T-010：Trace Chain 生成

状态：`done`

优先级：`P0`

依赖：T-004、T-009

任务内容：
- 为 bar 校验、策略计算、监督校验、signal 生成记录 trace。
- trace 记录 `input_ref`、`output_ref`、`module`、`status`、`error_msg`。

产出：
- `src/stock_agent/tracing.py`
- trace tests

验收标准：
- 每条 signal 必须有 `trace_id`。
- 用户可通过 `trace_id` 找到参与计算的 bar IDs。
- 失败路径也写入 trace，状态为 `failed` 或 `skipped`。

### T-011：Supervisor v1 校验

状态：`done`

优先级：`P0`

依赖：T-004、T-008、T-009、T-010

任务内容：
- 校验 bar schema。
- 校验策略 warm-up。
- 校验 signal 字段完整性。
- 校验 trace chain 完整性。
- 校验异常 bar 不触发提醒。
- 校验 expected signal regression。

产出：
- `src/stock_agent/supervisor/checks.py`
- supervisor tests

验收标准：
- supervisor 失败时不发送普通提醒。
- 失败原因写入 SQLite。
- 不调用 LLM。

### T-012：Signal 持久化与通知 Sink

状态：`done`

优先级：`P0`

依赖：T-005、T-009、T-010、T-011

任务内容：
- 将 approved signal 写入 SQLite。
- 写入 `notifications` 表。
- CLI notification sink 默认可用。
- Telegram sink 未配置时不阻塞 demo。

产出：
- `src/stock_agent/notifications/base.py`
- `src/stock_agent/notifications/cli_sink.py`
- `src/stock_agent/notifications/repository_sink.py`
- notification tests

验收标准：
- 发送失败最多重试 5 次。
- 多策略同 timestamp 可合并展示，但 SQLite raw signal 行保持独立。
- 无 Telegram token 时 demo 不失败。

### T-013：实现 `stock-agent run-demo`

状态：`done`

优先级：`P0`

依赖：T-003、T-005、T-007、T-008、T-009、T-010、T-011、T-012

任务内容：
- 串联配置加载、CSV provider、bar 校验、demo 策略、supervisor、SQLite、notification。
- 打印 demo summary。
- 生成确定性 signal。

产出：
- `src/stock_agent/commands/run_demo.py`
- integration test

验收标准：
- `stock-agent run-demo` 无网络可运行。
- 写入至少 1 条 signal。
- 输出与 `tests/fixtures/expected_signals/ma_cross_demo_2_3.json` 一致。

### T-014：实现 Health Monitor 与 `stock-agent health`

状态：`done`

优先级：`P0`

依赖：T-005、T-013

任务内容：
- 写入 `health_metrics`。
- 实现健康状态分类。
- `stock-agent health` 从 SQLite 查询当前状态、最近错误、最近心跳、延迟、告警失败数。

产出：
- `src/stock_agent/health/monitor.py`
- `src/stock_agent/commands/health.py`
- health tests

验收标准：
- `healthy`：最近 5 分钟有心跳，数据延迟 `< 60s`，错误率 `< 1%`。
- `degraded`：数据延迟 `60-300s`，错误率 `1%-5%`，连续失败 `< 3`。
- `unhealthy`：超过 5 分钟无心跳、数据延迟 `> 300s`、连续失败 `>= 3` 或核心模块退出。

## 5. P1 任务拆解

### T-101：正式 `ma_cross` 策略

状态：`done`

优先级：`P1`

依赖：T-008、T-010、T-011

任务内容：
- 实现正式 MA 策略。
- 默认 pairs：`MA3/MA5`、`MA5/MA10`、`MA10/MA20`。
- 黄金交叉：上一根 `short_ma <= long_ma`，当前 `short_ma > long_ma`。
- 死亡交叉：上一根 `short_ma >= long_ma`，当前 `short_ma < long_ma`。

验收标准：
- 历史窗口不足时不产信号。
- 正式 MA 不使用 `data/sample/sample_bars.csv` 的短样本作为完整回归判定。
- 后续若加入 regression，必须创建独立长 fixture。

### T-102：BOLL 带宽策略

状态：`done`

优先级：`P1`

依赖：T-008、T-010、T-011

任务内容：
- 实现 `middle`、`upper`、`lower`、`bandwidth`、`baseline_bandwidth`。
- 开口：`current_bandwidth >= baseline_bandwidth * 1.8`。
- 缩口：最近 3 个 bandwidth 位于 `[baseline * 0.8, baseline * 1.2]`。
- 开口有效性：最近 3 个 bandwidth 不低于 `baseline * 0.6`。
- 中轨附近震荡：最近 3 根 close 中至少 2 根满足 `abs(close - middle) / middle <= 0.005`。

验收标准：
- `window + 1` 前不产信号。
- 所有触发原因写入 `reason`。
- 支持 `buy_watch`、`sell_watch`、`observe`。

### T-103：Parquet Lake 写入

状态：`done`

优先级：`P1`

依赖：T-005、T-008、T-012

任务内容：
- 写入 `data/lake/raw_bars/date=YYYY-MM-DD/*.parquet`。
- 写入 `data/lake/features/date=YYYY-MM-DD/*.parquet`。
- 写入 `data/lake/signals/date=YYYY-MM-DD/*.parquet`。
- 写入 `data/lake/news/date=YYYY-MM-DD/*.parquet`。

验收标准：
- Parquet 不可用时早期可临时 JSONL，但必须保留 failing TODO test。
- DuckDB 可以直接读取 Parquet。

### T-104：CLI 查询模式

状态：`done`

优先级：`P1`

依赖：T-005、T-014

任务内容：
- 实现 `stock-agent cli`。
- 支持查询最近 signals。
- 支持查询 health。
- 支持查询 config changes。
- 支持查询 news cache。

验收标准：
- 查询只读操作不改变系统状态。
- 输出使用表格或清晰文本。
- 查询失败时返回可读错误。

### T-105：配置审核状态机

状态：`done`

优先级：`P1`

依赖：T-003、T-005、T-104

任务内容：
- 实现状态：`draft`、`pending_review`、`approved`、`rejected`、`applied`、`rollback`、`failed`。
- 保存 before/after diff。
- CLI review 后 approve/reject。
- approve 后原子写 YAML 并 reload。

验收标准：
- Telegram 发起的修改只能进入 pending，不直接执行。
- CLI approve 才能写回 YAML。
- reload 失败时恢复旧配置。

### T-106：Telegram Listener Skeleton

状态：`done`

优先级：`P1`

依赖：T-005、T-105

任务内容：
- 启动 `stock-agent telegram`。
- 校验 `allowed_user_ids`。
- 支持查询 signal、health、news。
- 支持发起配置修改请求，但不直接应用。

验收标准：
- 无 token 时命令给出清晰提示，不影响 demo。
- Telegram 状态变更必须进入 CLI 审核。
- 区分 `user` 和 `admin` 角色预留接口。

### T-107：按需新闻查询模块

状态：`done`

优先级：`P1`

依赖：T-003、T-005、T-104

任务内容：
- 实现 `NewsProvider` 接口。
- 实现 `NewsQueryService`。
- 支持 cache TTL。
- 未配置 provider 或 API key 时返回可读提示。

验收标准：
- 新闻只在 CLI/Telegram 明确查询时运行。
- 新闻不阻塞 worker、策略、supervisor。
- 新闻结果写入 `news_items`，保留原始 URL。

### T-108：Worker 调度骨架

状态：`done`

优先级：`P1`

依赖：T-003、T-005、T-014

任务内容：
- 实现 `stock-agent worker`。
- 预留 APScheduler 或 asyncio loop。
- 定期写 heartbeat。
- 支持单实例锁。
- 预留崩溃恢复与缺口补齐接口。

验收标准：
- worker 可启动和优雅停止。
- 重复启动时单实例锁生效。
- 心跳写入 `health_metrics`。

## 6. P2 后续增强

### T-201：Live Market Data Provider

状态：`done`

优先级：`P2`

依赖：M1 完成

任务内容：
- 评估 Twelve Data、Alpha Vantage、Polygon、marketstack、Nasdaq Data Link。
- 实现 live provider adapter。
- 处理 API key、额度、延迟、失败降级。

验收标准：
- 策略层只依赖标准 bar。
- 不让供应商响应结构泄漏到策略层。

### T-202：MACD 与 KDJ 正式策略

状态：`done`

优先级：`P2`

依赖：T-008、T-011

任务内容：
- MACD 默认关闭。
- KDJ 默认关闭。
- 提供足够长 fixture 后再加入 regression。

验收标准：
- 不使用 5 根 sample CSV 验证标准 MACD。
- warm-up 不足时不产信号。

### T-203：活跃策略 J 与扩展组合策略

状态：`done`

优先级：`P2`

依赖：T-202

任务内容：
- 实现 J 线强势策略。
- 首版支撑线算法不启用，退化为 MA80。
- 输出所有基础指标和阈值。

验收标准：
- 不产生无依据的买卖结论。
- 保留 source bar 和 trace chain。

### T-204：统计与知识层

状态：`done`

优先级：`P2`

依赖：T-005、T-103

任务内容：
- 日/月/年信号统计。
- 运行统计。
- 触发统计。
- 命中统计预留。

验收标准：
- 不计算收益、持仓、PnL。
- 汇总结果可被 CLI 查询。

### T-205：部署适配

状态：`done`

优先级：`P2`

依赖：M1 完成

任务内容：
- Mac `launchd` 示例。
- Linux `systemd` 示例。
- pm2 快速实验示例。

验收标准：
- 不硬编码本机路径。
- 支持环境变量和 config 路径配置。

## 7. P3 进阶盯盘闭环

### T-301：真实配置加载与运行时配置上下文

状态：`done`

优先级：`P3`

依赖：T-003、T-105、T-205

任务内容：
- 实现 `load_config(root, config_path)`，优先读取 `STOCK_AGENT_CONFIG` 或默认 `configs/config.yaml`。
- 所有 worker、query、telegram、news、provider、strategy 初始化都从配置文件读取，不再直接依赖 `DEFAULT_CONFIG`。
- 保存 `RuntimeConfigContext`，包含 config 内容、config_path、loaded_at、version/hash。
- 配置审核 approve 后 reload config，并记录 reload 成功或失败。

产出：
- `src/stock_agent/config_loader.py`
- 修改 `commands/*` 使用加载后的配置。
- config loader tests。

验收标准：
- 修改 YAML 后，不改代码即可影响 provider、symbols、strategies、storage path。
- `STOCK_AGENT_CONFIG=custom/config.yaml stock-agent init-config` 和 `stock-agent worker --once` 均可使用自定义配置。
- reload 失败时不污染当前运行配置。

完成记录：
- 已实现 `RuntimeConfigContext` 与 `load_config` / `reload_config`，入口优先读取 `STOCK_AGENT_CONFIG` 或 `configs/config.yaml`，缺省文件不存在时安全回退默认 demo 配置。
- 已接入 run-demo、worker、health、telegram、CLI query/config review，配置审核 approve 后通过重新加载目标 YAML 验证写回结果。
- 已补充配置加载、reload 失败保护、自定义 storage path、worker 环境变量配置路径等测试。

### T-302：Provider Registry 与数据源降级

状态：`done`

优先级：`P3`

依赖：T-201、T-301

任务内容：
- 实现 `ProviderRegistry`，按 config 选择 `csv_demo`、live provider 或 broker market data provider。
- 支持 provider priority、失败重试、额度/延迟错误分类。
- provider 返回统一 `Bar`，并附带 provider health、latency、request_id。
- 主数据源失败时按配置降级，并写入 trace、health_metrics、notifications。

产出：
- `src/stock_agent/providers/registry.py`
- provider fallback tests。

验收标准：
- 策略层仍只接收标准 `Bar`。
- live provider 失败时 demo/cache fallback 可被测试验证。
- 数据源降级会同时进入 SQLite 审计和通知 outbox。

完成记录：
- 已实现 `ProviderRegistry`，按 `provider.priority`、`provider.default`、`provider.fallback.order` 生成 provider 尝试顺序。
- 已支持 `csv_demo`、`live/alpha_vantage`、broker/cache 预留占位，并允许测试注入 provider factory。
- 已记录 provider attempt 的 health、latency、request_id、错误类型；主数据源失败后 fallback 成功会写入 `trace_chain`、`health_metrics` 和 `notifications`。
- 已补充 provider fallback 成功、全部失败、live 配置错误降级到 CSV 的测试。

### T-303：交易日历与盯盘窗口调度

状态：`done`

优先级：`P3`

依赖：T-301、T-302

任务内容：
- 实现美股交易日历接口，支持常规交易日、周末、节假日、半日市预留。
- 根据 `premarket_lead`、`regular_session`、`close_focus_window`、`afterhours_tail` 生成 watch windows。
- 策略默认只在 regular session bar 上运行；盘前/盘后只展示或单独标记。
- 提供 `stock-agent cli schedule` 查询今天/下一次盯盘计划。

产出：
- `src/stock_agent/scheduler/market_calendar.py`
- `src/stock_agent/scheduler/watch_windows.py`
- schedule tests。

验收标准：
- 非交易日不触发策略计算，只记录健康与状态。
- 常规交易时段默认 `09:30-16:00 America/New_York`。
- 时间内部 UTC，用户展示可转换本机或美东时间。

完成记录：
- 已实现 `USMarketCalendar`，支持周末、规则型美股闭市日、Good Friday、Juneteenth、Thanksgiving、Christmas 等常见假期，以及 Independence Day 前一交易日、Black Friday、Christmas Eve 等半日市预留。
- 已实现 `build_watch_schedule`，按配置生成 `premarket`、`regular`、`close_focus`、`afterhours`、`closed` 窗口；盘前/盘后标记为展示用途，常规/收盘重点窗口标记为策略可用。
- 已新增 `schedule` 配置段并同步 `configs/config.yaml`，默认 `America/New_York`、`09:30-16:00`、盘前/收盘重点/盘后各 60 分钟。
- 已接入 `stock-agent cli schedule`，无需 runtime SQLite 即可查询今日/下一交易日盯盘计划；Telegram 骨架同步支持 `/schedule` 只读查询。
- 已补充常规交易日、周末、闭市日、半日市和 CLI schedule 测试。

### T-304：1m/tick 到 30m Bar 聚合与缺口补齐

状态：`done`

优先级：`P3`

依赖：T-008、T-103、T-303

任务内容：
- 实现从 tick/1m bar 聚合成 30m bar。
- 聚合规则：open 第一笔、high 最高、low 最低、close 最后一笔、volume 汇总、vwap 可选。
- 写入 checkpoint，记录每个 symbol/interval 的最后成功窗口。
- 启动时检测缺口，补拉缺失窗口并重建相同时间节点。
- 缺失、重复、乱序、插值或替代数据必须标记 `quality_flag`。

产出：
- `src/stock_agent/bars/aggregator.py`
- `src/stock_agent/bars/gap_fill.py`
- aggregation/gap fill tests。

验收标准：
- 同一输入可 deterministic 生成相同 bar_id 和 30m bar。
- 异常或插值 bar 不直接触发普通 signal。
- checkpoint 可防止重复计算已完成窗口。

完成记录：
- 已实现 `aggregate_to_interval`，将 `1m` 标准 bar 聚合为 `30m` bar，窗口口径为 `(window_start, window_end]`，输出 timestamp 使用 `window_end`。
- 已实现 OHLCV/VWAP 聚合规则：open 取窗口第一根、high/low 取极值、close 取最后一根、volume 汇总、vwap 按成交量加权。
- 已实现 `quality_flag` 标记：缺失窗口点标记 `missing`，重复时间戳标记 `duplicate`，乱序输入标记 `out_of_order`，插值补齐标记 `interpolated|missing`。
- 已实现 checkpoint repository 与 `update_bar_checkpoint`，记录每个 `symbol/interval` 的最后成功窗口。
- 已实现 `detect_missing_windows` 与 `build_interpolated_bar`，用于启动时检测缺口并保留相同 30m 时间节点；插值 bar 默认不属于普通 `normal` 数据。
- 已补充聚合、缺口检测、checkpoint、BarBuilder 聚合入口测试。

### T-305：Strategy Engine 与 Signal Pipeline

状态：`done`

优先级：`P3`

依赖：T-101、T-102、T-202、T-203、T-301、T-304

任务内容：
- 实现统一 `StrategyEngine`，根据配置启用/关闭策略并传入参数。
- 支持 `ma_cross`、`boll`、`macd`、`kdj`、`active_j`。
- 每个策略输出标准 `Signal`，并写入 strategy_snapshot 和 trace_chain。
- `run-demo` 可继续使用 demo strategy，但 worker 使用正式 Strategy Engine。

产出：
- `src/stock_agent/strategies/engine.py`
- `src/stock_agent/signals/pipeline.py`
- strategy engine tests。

验收标准：
- 配置关闭的策略不会执行。
- warm-up 不足时不产 signal，并记录 skipped trace。
- 同一批 bar 多策略触发时，raw signals 独立、通知可合并。

完成记录：
- 已实现 `StrategyEngine`，按 `config.strategies` 启停 `ma_cross`、`boll`、`macd`、`kdj`、`active_j`，并把 YAML 参数传入各策略函数。
- 已补充 `active_j` 默认配置，默认关闭，不改变首版默认运行行为。
- 已实现 warm-up 检查，不足窗口时不产 signal，并记录 `skipped` trace。
- 已实现 `SignalPipeline`，执行正式 Strategy Engine，生成 `StrategySnapshot`，并将 snapshot 与 signal/skipped trace 写入 SQLite 审计表。
- 已新增 `strategy_snapshots` SQLite 表与 repository round-trip。
- 已验证同一批 bar 多策略触发时 raw signals 保持独立，后续通知合并留给 T-307。

### T-306：Worker 盯盘 Pipeline 集成

状态：`done`

优先级：`P3`

依赖：T-302、T-303、T-304、T-305、T-011、T-012、T-014

任务内容：
- 将 worker tick 从 heartbeat skeleton 升级为完整 pipeline：
  provider fetch -> bar validate/aggregate -> persist lake/sqlite -> strategy engine -> supervisor -> signal persist -> notification outbox -> health。
- 支持 `worker --once`、循环运行、优雅停止、单实例锁。
- 保证新闻查询和对话不阻塞盯盘 pipeline。
- 每轮 tick 输出结构化 summary。

产出：
- `src/stock_agent/worker/pipeline.py`
- 修改 `src/stock_agent/worker/scheduler.py`
- worker integration tests。

验收标准：
- `stock-agent worker --once` 可用 demo provider 跑出完整 summary。
- 可通过固定长样本触发 MA/BOLL 等正式策略 signal。
- provider/strategy/supervisor/notification 任一失败，health 可见且不会造成 silent failure。

完成记录：
- 已实现 `WorkerPipeline`，串联 `ProviderRegistry -> BarBuilder -> LakeWriter/checkpoint -> SignalPipeline -> Supervisor -> signal persistence -> notification sink -> health`。
- 已改造 `Worker.tick()` 支持 pipeline，同时保留无 pipeline 时的 heartbeat 行为，确保旧的直接 Worker 用法仍可用。
- 已改造 `stock-agent worker --once` 输出 `last_tick_summary`，包含 provider、raw/prepared bars、candidate/approved/rejected signals、notifications、lake_writes、trace_count、errors。
- 已保持单实例锁、循环运行、优雅停止逻辑不变；pipeline 异常会被 worker 捕获并写入 health，不会 silent failure。
- 已补充 worker 集成测试：demo provider summary、自定义 storage path、正式 `ma_cross` 信号持久化、snapshot 写入、notification 写入和 lake raw_bars 写入。

### T-307：通知 Outbox、合并展示与幂等发送

状态：`done`

优先级：`P3`

依赖：T-012、T-305、T-306

任务内容：
- 实现 notification outbox 状态：`pending`、`sending`、`sent`、`failed`、`suppressed`。
- 同一 symbol/timestamp 多策略 signal 合并成一条人类可读提醒。
- 原始 signal 行保持独立，通知记录保存关联 signal_ids。
- 发送失败最多重试 5 次，重启后可继续发送 pending 通知。

产出：
- `src/stock_agent/notifications/outbox.py`
- `src/stock_agent/notifications/formatter.py`
- notification outbox tests。

验收标准：
- 重复 worker tick 不重复发送已 sent 的同一 notification。
- CLI sink 与 Telegram sink 使用同一格式化输入。
- 发送失败可查询、可重试、可审计。

完成记录：
- 已实现 `notifications/formatter.py`，按 `symbol + timestamp` 合并多策略 signal，生成统一人类可读消息，并保留每条原始 `signal_id`。
- 已实现 `notifications/outbox.py`，复用 `notifications` 表支持 `pending`、`sending`、`sent`、`failed`、`suppressed` 状态。
- 已实现 deterministic `notification_id`，基于 `channel + signal_ids` 幂等生成；重复 worker tick 不会重复发送已 `sent` 的同一提醒。
- 已实现 pending/failed 通知重试，失败最多 5 次，达到上限后标记 `suppressed`，并保留 `retry_count` 与 `error_msg` 供 CLI/Telegram 查询。
- 已改造 worker pipeline 使用 outbox enqueue/dispatch，不再直接发送；CLI sink 使用 outbox payload 中的统一 message，后续 Telegram sink 可复用同一 payload。
- 已补充 formatter、outbox 幂等、失败重试/suppressed、worker 重复 tick 不重复通知测试。

### T-308：信号追溯详情查询

状态：`done`

优先级：`P3`

依赖：T-010、T-305、T-306

任务内容：
- 增强 trace_chain，保存指标输入窗口、指标计算值、触发阈值、supervisor 决策。
- 实现 `stock-agent cli trace SIGNAL_ID|TRACE_ID`。
- Telegram 支持 `/trace SIGNAL_ID` 查询只读追溯摘要。

产出：
- `src/stock_agent/commands/trace.py`
- trace query tests。

验收标准：
- 用户可看到某条提醒基于哪几根 bar、哪些指标值、为什么触发。
- 追溯查询不调用 LLM，不生成新的买卖结论。
- trace 缺失时返回可读错误。

完成记录：
- 已实现 `src/stock_agent/commands/trace.py`，通过 QueryService 支持 `stock-agent cli trace SIGNAL_ID|TRACE_ID` 只读追溯查询。
- Telegram `/trace` 查询已复用 QueryService，不直接访问 repository，也不调用 LLM 生成新结论。
- 已补充 trace query tests，并通过 `tests/test_trace_query.py` 验收。

### T-309：历史数据查询与重放

状态：`done`

优先级：`P3`

依赖：T-103、T-304、T-305

任务内容：
- 实现 `stock-agent cli bars --symbol SYMBOL --from ... --to ...`。
- 实现 `stock-agent replay --from ... --to ... --symbols ...`，从 lake/SQLite 重放 bar 并复算 signal。
- replay 默认 dry-run，不发送普通通知。
- replay 结果可写入独立 trace 或 regression report。

产出：
- `src/stock_agent/commands/bars.py`
- `src/stock_agent/commands/replay.py`
- replay tests。

验收标准：
- 同一历史输入多次 replay 输出 deterministic。
- replay 不污染正常运行 signal，除非显式 `--persist`。
- 可用于排查历史提醒。

完成记录：
- 已实现 `src/stock_agent/commands/bars.py` 与 `src/stock_agent/commands/replay.py`，支持历史 bar 查询和 dry-run replay。
- replay 默认不持久化、不发送普通通知；仅在显式 `--persist` 时写入 signal。
- 已补充 bars/replay/query service tests，并通过 `tests/test_bars_replay.py` 与 `tests/test_query_service.py` 验收。

### T-310：端到端盯盘回归样本

状态：`done`

优先级：`P3`

依赖：T-305、T-306、T-307

任务内容：
- 新增长样本 CSV 或 JSONL，用于正式 MA/BOLL/MACD/KDJ/Active J 的端到端 regression。
- 每个策略至少有一个 deterministic expected signal 或明确无信号样例。
- 样本不替代 `data/sample/sample_bars.csv` 的 demo 用途，需独立命名。

产出：
- `data/sample/regression_bars_long.csv`
- `tests/fixtures/expected_signals/*.json`
- e2e strategy regression tests。

验收标准：
- 不再用 5 根 demo bar 验证正式策略。
- `worker --once --config tests/fixtures/configs/regression.yaml` 可 deterministic 通过。
- expected signal 包含 source_bar_ids 和 trace_id。

完成记录：
- 已新增独立长样本 `data/sample/regression_bars_long.csv`，不替代 5 根 demo bar。
- 已新增 `tests/fixtures/configs/regression.yaml`，使用正式 MA/BOLL/MACD/KDJ/Active J 策略实现与短窗口参数进行 deterministic 回归。
- 已新增 `tests/fixtures/expected_signals/formal_regression.json`，覆盖每个正式策略至少一个 expected signal，且包含 `source_bar_ids` 与 `trace_id`。
- 已新增端到端 worker regression test，验证 26 根历史 bar 生成 35 条 approved signals，覆盖 5 个正式策略。
- 已支持 `stock-agent worker --once --config tests/fixtures/configs/regression.yaml`。

## 8. P4 自然语言与双入口增强

### T-401：统一 Command Intent Schema

状态：`done`

优先级：`P4`

依赖：T-104、T-105、T-106

任务内容：
- 定义所有 CLI/Telegram 可执行意图的 JSON schema。
- 意图分为 read_only、pending_change、local_admin、high_risk_blocked。
- 支持查询 signals、health、bars、news、stats、trace、schedule。
- 支持发起 add_symbol、remove_symbol、enable_strategy、disable_strategy、change_watch_window 等 pending change。

产出：
- `src/stock_agent/dialog/intents.py`
- intent schema tests。

验收标准：
- 所有自然语言解析结果必须先落到 intent schema。
- 未知或模糊意图必须 ask clarification，不得猜测执行。
- 高风险意图不会执行，只返回安全提示。

完成记录：
- 已新增 `src/stock_agent/dialog/intents.py`，定义 read_only、pending_change、local_admin、high_risk_blocked 四类可路由意图，以及 clarification 安全澄清意图。
- 已覆盖 signals、health、bars、news、stats、trace、schedule 查询意图。
- 已覆盖 add_symbol、remove_symbol、enable_strategy、disable_strategy、change_watch_window 配置变更意图。
- 已确保高风险交易/账户类 intent 不可执行，只返回安全提示。
- 已新增 intent schema tests。

### T-402：结构化命令解析器

状态：`done`

优先级：`P4`

依赖：T-401

任务内容：
- 实现无需 LLM 的结构化命令解析，例如：
  `add symbol QQQ`、`enable strategy macd`、`show signals NVDA limit 5`。
- CLI 和 Telegram 共用 parser。
- parser 只生成 intent，不直接执行业务。

产出：
- `src/stock_agent/dialog/parser.py`
- parser tests。

验收标准：
- 无 LLM key 时仍可完成常用查询和 pending change 创建。
- 解析失败返回可读错误和可用示例。
- 变更命令在 Telegram 侧只能进入 pending_review。

完成记录：
- 已新增 `src/stock_agent/dialog/parser.py`，提供无需 LLM 的结构化命令解析。
- 已支持 `show signals NVDA limit 5`、health/news/stats/bars/trace/schedule 等常用查询。
- 已支持 add/remove symbol、enable/disable strategy、change watch window 等 pending change intent。
- 已在 parser 层拦截下单、撤单、转账、提现、账户/密码修改等高风险命令。
- 已新增 parser tests，验证失败时返回 clarification intent 和可用示例。

### T-403：LLM 自然语言解析与安全护栏

状态：`done`

优先级：`P4`

依赖：T-401、T-402

任务内容：
- 实现可选 LLM parser，将自然语言转为 Command Intent。
- LLM 不得直接访问数据库写接口、策略计算接口或外部交易接口。
- LLM 输出必须经过 schema validation、权限检查和风险分类。
- 对“确定买入”“保证收益”“替我下单”等话术进行安全改写或拒绝。

产出：
- `src/stock_agent/dialog/llm_parser.py`
- `src/stock_agent/dialog/safety.py`
- LLM parser mock tests。

验收标准：
- 测试不需要真实 LLM key。
- LLM 输出非法 JSON、越权 intent、模糊 symbol 时都不会执行。
- 指标计算和 signal 生成仍完全由确定性代码完成。

完成记录：
- 已新增 `src/stock_agent/dialog/llm_parser.py`，支持可注入 mock client 的可选 LLM parser。
- 已新增 `src/stock_agent/dialog/safety.py`，在 LLM 前后执行高风险文本拦截、schema validation、权限检查和模糊 symbol 检查。
- LLM 只能输出 Command Intent JSON，不访问数据库、策略计算、配置写入或外部交易接口。
- 已覆盖非法 JSON、越权 local_admin、模糊 symbol、高风险话术提前拦截等 mock tests。

### T-404：CLI 交互式 Shell

状态：`done`

优先级：`P4`

依赖：T-401、T-402、T-104、T-308、T-309

任务内容：
- 实现 `stock-agent cli` 交互模式。
- 支持自然语言/结构化命令输入、历史查询、配置审核、trace 查询。
- CLI 可直接执行低风险本地管理命令；高风险和配置变更需清晰确认。

产出：
- `src/stock_agent/commands/interactive_cli.py`
- interactive CLI tests。

验收标准：
- 输入 `最近 NVDA 有什么信号` 可转成 signal query。
- 输入 `添加 QQQ 到关注` 在 CLI 可走受控配置变更流程。
- 输入下单、转账、改密码等高风险指令必须拒绝。

完成记录：
- 已新增 `src/stock_agent/commands/interactive_cli.py`，实现 `stock-agent cli` 交互模式。
- 已复用结构化 parser 与 QueryService，支持查询 signals/health/news/stats/bars/trace/schedule。
- 已支持轻量中文输入 `最近 NVDA 有什么信号` 与 `添加 QQQ 到关注`。
- 配置变更需输入 `yes` 才会记录为 `pending_review`，后续仍需 CLI approve。
- 高风险交易/账户类指令会被拒绝并返回安全提示。

### T-405：真实 Telegram Bot Listener

状态：`done`

优先级：`P4`

依赖：T-401、T-402、T-403、T-307

任务内容：
- 接入 `python-telegram-bot` 或等价 SDK。
- 支持 `/signals`、`/health`、`/news`、`/trace`、`/config` 和自然语言消息。
- 支持 allowed_user_ids、admin_user_ids、chat_id allowlist。
- Telegram 变更意图必须创建 pending_change，不直接写 YAML。

产出：
- `src/stock_agent/telegram/bot.py`
- Telegram bot mock tests。

验收标准：
- 无 token 时命令清晰退出，不影响 worker。
- user/admin 权限边界可测试。
- Telegram 查询与 CLI 查询使用同一 query service。

完成记录：
- 已新增 `src/stock_agent/telegram/bot.py`，提供 mock-friendly 的 Telegram bot adapter core，并检测可选 `python-telegram-bot` SDK。
- 已支持 `/signals`、`/health`、`/news`、`/trace`、`/config` 等 slash 命令复用现有 listener 与 QueryService。
- 已支持自然语言/结构化消息通过 LLM parser fallback 生成 intent，再路由到 QueryService 或 pending config change。
- 已支持 allowed_user_ids、admin_user_ids、allowed_chat_ids 权限边界。
- Telegram 配置变更只创建 `pending_review`，不直接写 YAML。
- 已新增 Telegram bot mock tests。

### T-406：查询服务统一化

状态：`done`

优先级：`P4`

依赖：T-104、T-308、T-309、T-401

任务内容：
- 将 signals、health、news、stats、bars、trace、schedule 查询统一到 `QueryService`。
- CLI 和 Telegram 只负责输入输出，不直接访问 repository。
- 查询结果输出支持文本表格和 Telegram-friendly 简短格式。

产出：
- `src/stock_agent/query/service.py`
- query service tests。

验收标准：
- 同一 query intent 在 CLI/Telegram 下返回相同核心数据。
- 查询服务只读，不修改运行状态，除非明确允许写缓存类记录。
- 没有 runtime DB 时返回可读错误。

完成记录：
- 已新增 `src/stock_agent/query/service.py`，统一 signals、health、news、stats、bars、trace、schedule、config-changes 查询。
- 已将 CLI query、bars、trace 命令切到 QueryService。
- 已将 Telegram listener 的 read-only 查询切到 QueryService，不再通过 CLI wrapper 查询。
- 查询输出保留文本表格，并支持 Telegram-friendly strip 格式。
- 已新增 query service tests，覆盖缺失 runtime DB、核心表查询、bars lake 查询、trace 查询，以及 CLI/Telegram 共享核心查询结果。

## 9. P5 券商或交易相关 API 安全接入

### T-501：Broker Adapter 安全接口定义

状态：`done`

优先级：`P5`

依赖：T-301、T-401

任务内容：
- 定义 broker adapter 接口，首版只允许 read-only：
  market data、account snapshot、positions snapshot、broker health。
- 明确禁止 order placement、order modification、withdrawal、password/account mutation。
- 所有 broker capability 必须显式声明，并默认关闭。

产出：
- `src/stock_agent/broker/base.py`
- broker capability tests。

验收标准：
- 代码库中不存在可被 worker/telegram 直接调用的真实下单函数。
- 若 adapter 暴露 order 相关方法，默认实现必须 raise `BrokerActionBlocked`。
- 安全测试覆盖 Telegram/LLM/CLI 下单请求拒绝。

完成记录：
- 已新增 `src/stock_agent/broker/base.py`，定义 read-only broker adapter 边界。
- 已定义 market data、account snapshot、positions snapshot、broker health 只读能力声明。
- 所有 capability 默认关闭，交易/资金/账户变更方法默认 raise `BrokerActionBlocked`。
- 已新增 broker capability tests，并覆盖 CLI/LLM/Telegram 下单请求拒绝。

### T-502：Broker Market Data Provider

状态：`done`

优先级：`P5`

依赖：T-501、T-302

任务内容：
- 将券商行情 API 作为 provider 的一种来源接入。
- 仍输出标准 `Bar` 或 quote schema，不暴露 broker 原始响应给策略层。
- 支持 sandbox/paper endpoint 和 live endpoint 明确区分。

产出：
- `src/stock_agent/providers/broker_market_data.py`
- broker market data mock tests。

验收标准：
- 没有 broker key 时不影响 demo。
- sandbox/live 环境必须在配置和日志中明确标记。
- broker provider 失败可降级到其他 provider。

完成记录：
- 已新增 `src/stock_agent/providers/broker_market_data.py`，通过 broker adapter 提供只读行情 provider。
- provider 默认未配置，缺少 broker adapter 时返回清晰 configuration 错误。
- sandbox/paper/live 环境在 provider health 与错误信息中明确标记，live 默认禁用。
- 已接入 ProviderRegistry，broker provider 失败时可 fallback 到 csv_demo。
- 已新增 broker market data mock tests。

### T-503：Credential 与权限安全

状态：`done`

优先级：`P5`

依赖：T-501、T-502

任务内容：
- 实现 secret 读取策略：只从环境变量或本地安全配置读取。
- 日志、trace、SQLite、Parquet 中不得保存 token、account id 明文或敏感 header。
- 增加 secret redaction 工具。
- 启动时检查 broker key 权限，提示是否含交易权限。

产出：
- `src/stock_agent/security/secrets.py`
- `src/stock_agent/security/redaction.py`
- security tests。

验收标准：
- 单元测试验证敏感字段不会出现在日志、trace、notification。
- 检测到交易权限时默认降级为 disabled，并要求 CLI 明确确认是否继续使用只读功能。
- 不允许 Telegram 或 LLM 请求读取密钥。

完成记录：
- 已新增 `src/stock_agent/security/secrets.py` 与 `src/stock_agent/security/redaction.py`，统一 secret 读取策略和递归敏感信息清洗。
- secret 只允许从 `env:NAME` 或显式传入的本地安全配置读取；Telegram/LLM source 请求读取 secret 会被拒绝。
- SQLite repository 与 Parquet/JSONL lake 写入边界已接入 redaction，覆盖 trace、notification、health details、统计 details、配置变更 diff 等自由 payload。
- Provider Registry、live provider、broker health/provider health 输出已接入 redaction，provider 错误消息不会持久化 token/account/header 明文。
- Broker market data provider 检测到 adapter 暴露 order、withdrawal 或 account mutation 权限时默认拒绝启用。
- 结构化命令与 LLM safety marker 已拦截读取 api key、token、secret、credential、密钥等请求。
- 已新增 `tests/test_security.py` 覆盖 secret 读取、redaction、SQLite/lake 清洗、broker 权限降级与 Telegram/CLI 风格 secret 读取拦截。

### T-504：交易动作防火墙

状态：`done`

优先级：`P5`

依赖：T-401、T-403、T-501、T-503

任务内容：
- 实现 `TradingActionFirewall`，拦截所有下单、撤单、改账户、转账、改密码类 intent。
- 提供统一拒绝文案：本系统只提供观察信号，最终买卖由用户自行决定。
- 所有被拦截请求写入安全审计。

产出：
- `src/stock_agent/security/trading_firewall.py`
- trading firewall tests。

验收标准：
- CLI/Telegram/LLM 三条入口的下单请求全部被拦截。
- 拦截不影响普通行情和信号查询。
- 审计记录不包含敏感账户信息。

完成记录：
- 已新增 `src/stock_agent/security/trading_firewall.py`，提供统一 `TradingActionFirewall`、拒绝文案和 blocked decision。
- 已新增 `security_audit` SQLite 表，以及 `insert_security_audit` / `list_security_audit` repository 方法。
- CLI interactive high-risk intent 已接入防火墙，拦截后写入 security audit，并返回统一观察信号边界文案。
- Telegram bot natural-language high-risk intent 已接入防火墙，拦截下单、撤单、转账、改账户、改密码、读取密钥等动作并审计。
- LLM parser 产出的 high-risk intent 可由同一防火墙处理，避免进入 QueryService、config service、provider、broker 或策略层。
- 审计写入复用 redaction，raw_text、actor_ref、details 中的 account id、token、secret 等敏感信息不会明文落库。
- 已新增 `tests/test_trading_firewall.py`，覆盖 CLI/Telegram/LLM 下单拦截、普通信号查询不受影响、审计脱敏。

## 10. P6 监督 Agent、质量与可观测性

### T-601：Supervisor 独立指标复算

状态：`done`

优先级：`P6`

依赖：T-305、T-306

任务内容：
- 用独立实现复算 MA、BOLL、MACD、KDJ、Active J 核心指标。
- 对主策略输出的指标值、交叉状态、阈值判断做一致性校验。
- 不使用 LLM 复算指标。

产出：
- `src/stock_agent/supervisor/recompute.py`
- recompute tests。

验收标准：
- 主策略与 supervisor 结果不一致时 signal 被 suppress。
- supervisor 结果写入 trace_chain 和 health_metrics。
- 可通过固定 fixture 触发一致和不一致两类场景。

完成记录：
- 已新增 `src/stock_agent/supervisor/recompute.py`，独立复算 MA、BOLL、MACD、KDJ、Active J 核心触发状态。
- `supervise_candidate_signals` 已接入 recompute check；复算方向与策略 signal 不一致时拒绝该 signal，不进入普通通知链路。
- supervisor recompute 结果会写入 `trace_chain`，并通过 `health_metrics` 记录健康或 mismatch 状态。
- Worker pipeline 已将策略参数快照传入 supervisor，保证复算使用同一配置但独立算法。
- 顺手修正 `stock-agent worker` 正常完成路径返回 `0/1` 的缩进问题，避免 worker 验收返回 `None`。
- 已新增 `tests/test_supervisor_recompute.py`，并更新 supervisor checks 测试覆盖成功和失败 trace。
- 验证命令：`pytest tests/test_supervisor_recompute.py tests/test_supervisor_checks.py tests/test_worker.py tests/test_strategy_engine_pipeline.py`，结果 22 passed。

### T-602：多数据源交叉校验

状态：`done`

优先级：`P6`

依赖：T-302、T-601

任务内容：
- 对关键 symbol 的 close、volume、timestamp 做主备数据源对比。
- 配置偏差阈值，例如 price_diff_bps、volume_diff_ratio、max_timestamp_skew_sec。
- 偏差超阈值时标记 data_quality，并抑制普通信号。

产出：
- `src/stock_agent/supervisor/provider_compare.py`
- provider compare tests。

验收标准：
- 主备数据偏差异常会进入 degraded/unhealthy 指标。
- 偏差结果可被 CLI/Telegram 查询。
- 无备源时不阻塞 demo，但记录 compare skipped。

完成记录：
- 已新增 `src/stock_agent/supervisor/provider_compare.py`，支持 close bps、volume ratio、timestamp skew 三类主备 provider bar 对比。
- compare 结果支持 `ok`、`skipped`、`degraded`、`unhealthy` 状态；无备源时记录 skipped，不阻塞 demo/worker。
- 偏差超阈值时可通过 `apply_compare_quality` 标记 `provider_compare_degraded/unhealthy`，并提供 `should_suppress_signals` 给 pipeline 抑制普通信号。
- Worker pipeline 已记录 provider compare trace 与 health；当前 demo 单源路径写入 skipped 状态。
- QueryService、CLI action `provider-compare`、Telegram `/provider-compare` 已支持查询最近 compare trace。
- 已新增 `tests/test_provider_compare.py`，覆盖正常、异常、skipped、health/trace 持久化和 CLI/Telegram 查询。
- 验证命令：`pytest tests/test_provider_compare.py tests/test_query_service.py tests/test_telegram_listener.py tests/test_telegram_bot.py tests/test_worker.py`，结果 29 passed。

### T-603：异常 Bar 隔离区

状态：`done`

优先级：`P6`

依赖：T-304、T-601

任务内容：
- 建立 abnormal bar quarantine 流程。
- 异常类型包括跳价、零/负价格、负成交量、重复 timestamp、乱序、缺失窗口。
- 隔离 bar 可查询、可手动标记 accepted/rejected。

产出：
- `src/stock_agent/bars/quarantine.py`
- quarantine tests。

验收标准：
- 被隔离 bar 不进入普通策略计算。
- 用户可查询异常原因和影响的 symbol/window。
- 手动接受异常 bar 必须走 CLI 审核并留审计。

完成记录：
- 已新增 `src/stock_agent/bars/quarantine.py`，支持 zero/negative price、negative volume、OHLC 异常、重复 timestamp、乱序、缺失窗口、跳价异常检测。
- 已新增 `abnormal_bars` SQLite 表，以及 abnormal bar insert/list/update repository 方法。
- Worker pipeline 已在 BarBuilder 和策略计算前执行 quarantine，只将 clean bars 送入普通策略。
- 已支持 `review_quarantined_bar` 将隔离 bar 标记为 `accepted` 或 `rejected`，并记录 reviewer 与 note。
- QueryService、CLI action `abnormal-bars`、Telegram `/abnormal-bars` 已支持查询隔离原因、symbol/window、状态和 bar_id。
- 已新增 `tests/test_bar_quarantine.py`，覆盖异常隔离、策略前过滤、持久化查询和手动 review 状态。
- 验证命令：`pytest tests/test_bar_quarantine.py tests/test_query_service.py tests/test_telegram_listener.py tests/test_telegram_bot.py tests/test_worker.py`，结果 28 passed。

### T-604：提示文案安全审查

状态：`done`

优先级：`P6`

依赖：T-307、T-403、T-601

任务内容：
- 在通知发送前检查文案是否包含确定性收益、保证性语言或自动交易暗示。
- 将“买入/卖出”统一包装为“买入观察/卖出观察/提醒”，除非用户明确只要原始 signal。
- LLM 生成摘要时必须通过文案审查。

产出：
- `src/stock_agent/supervisor/message_safety.py`
- message safety tests。

验收标准：
- 违规文案会被修正或 suppress。
- signal reason 原始客观数据保留，用户可追溯。
- 文案审查不改变指标计算结果。

完成记录：
- 已新增 `src/stock_agent/supervisor/message_safety.py`，审查保证收益、确定性语言和自动交易暗示。
- NotificationOutbox 已在通知入 outbox 前执行 message safety；可修正文案进入 pending，自动交易暗示直接 `suppressed`。
- 出站 payload 中新增 `message_safety` 元数据，原始 signal reason 保留在 `signals` 列表中，不改变指标计算结果。
- 已新增 `tests/test_message_safety.py`，覆盖文案修正、自动交易 suppress、outbox payload 保留原始 reason。
- 已修正 notification 测试连接关闭，避免 Windows 临时 SQLite 文件句柄影响回归。
- 验证命令：`pytest tests/test_message_safety.py tests/test_notifications.py tests/test_worker.py`，结果 22 passed。

### T-605：健康指标与可观测性扩展

状态：`done`

优先级：`P6`

依赖：T-306、T-307、T-601

任务内容：
- 扩展 health_metric details：抓数成功率、数据延迟、提醒延迟、数据源降级次数、异常 bar 数、supervisor 拦截数、配置审核积压。
- 实现 `stock-agent cli health --verbose`。
- 输出最近错误、最近失败 trace、最近 provider fallback。

产出：
- `src/stock_agent/health/observability.py`
- health verbose tests。

验收标准：
- health 能区分 provider、bar_builder、strategy、supervisor、notification 子模块。
- 连续失败和核心模块退出会进入 unhealthy。
- 不泄漏 secret。

完成记录：
- 已新增 `src/stock_agent/health/observability.py`，汇总 provider、bar_builder、strategy、supervisor、notification、worker 等模块状态。
- `stock-agent health --verbose` 已支持输出 provider 成功率、fallback 次数、异常 bar 数、supervisor 拦截数、通知 pending/failed、配置审核积压、最近失败 trace/provider fallback。
- health verbose 输出复用 redaction，不泄漏 api key、token、secret 等敏感字段。
- 已修正 `run_health` SQLite 连接关闭，避免 Windows 临时数据库文件句柄影响测试。
- 已新增 `tests/test_observability.py`，覆盖 verbose 输出、模块状态、积压统计和脱敏。
- 验证命令：`pytest tests/test_observability.py tests/test_health_monitor.py tests/test_cli_entrypoint.py tests/test_worker.py`，结果 24 passed。

## 11. P7 生产化与长期运行

### T-701：崩溃恢复与重启预算

状态：`done`

优先级：`P7`

依赖：T-306、T-605

任务内容：
- 记录 worker crash count、restart attempts、last_failure。
- 连续崩溃 10 次后停止当前运行单元。
- 恢复流程最多尝试 5 次，失败后彻底停止并通知。

验收标准：
- crash budget 可被测试模拟。
- 达到阈值后不会无限重启刷屏。
- 故障通知进入 outbox。

完成记录：
- 已新增 `src/stock_agent/worker/recovery.py`，提供 `CrashRecoveryManager`、`CrashBudgetState` 与 `CrashBudgetExceeded`。
- Worker 已接入 crash budget；连续 tick 异常达到阈值后停止当前运行单元，避免无限重启刷屏。
- 启动恢复路径已记录 recovery attempt，恢复预算耗尽后停止并写入 worker failure notification outbox。
- crash_count、restart_attempts、last_failure、stopped 状态写入 checkpoint，便于后续查询和恢复。
- 已新增 `tests/test_worker_recovery.py`，覆盖 crash budget、worker 停止、防故障刷屏和故障通知。
- 验证命令：`pytest tests/test_worker_recovery.py tests/test_worker.py tests/test_notifications.py`，结果 21 passed。

### T-702：多实例身份与锁策略

状态：`done`

优先级：`P7`

依赖：T-108、T-306

任务内容：
- 增加 `instance_id`、`host_id`、`lock_owner`。
- 单机使用文件锁，未来多机预留 SQLite/外部锁实现。
- 通知和 signal 幂等键包含 instance 维度但不造成重复提醒。

验收标准：
- 同一机器重复启动仍被阻止。
- 不同 instance 的写入可审计来源。
- 多机模式默认 disabled。

完成记录：
- 已新增 `src/stock_agent/worker/identity.py`，提供 `WorkerIdentity` 与 `build_worker_identity`，支持 `instance_id`、`host_id`、`lock_owner`。
- `SingleInstanceLock` 已写入 pid、host_id、instance_id、lock_owner、multi_instance_enabled；同一机器重复启动仍被阻止。
- Worker 和 WorkerPipeline 已共享同一 identity，并将 instance/host/lock_owner 写入 health details。
- NotificationOutbox payload 已写入 `instance_id`，但 notification id 仍基于 channel + signal_ids，避免不同 instance 重复提醒。
- 多机模式通过 `STOCK_AGENT_MULTI_INSTANCE` 预留，默认 disabled。
- 已新增 `tests/test_worker_identity.py`，覆盖锁文件身份、重复启动阻止、health/notification 审计来源和幂等通知。
- 验证命令：`pytest tests/test_worker_identity.py tests/test_worker.py tests/test_notifications.py`，结果 21 passed。

### T-703：数据保留与清理任务

状态：`done`

优先级：`P7`

依赖：T-103、T-204、T-309

任务内容：
- 实现日终清理：保留统计、审计、trace 必要信息，清理临时数据。
- 新闻一周后压缩为标题+概要+链接，一个月后按公司/市场保留代表新闻。
- 清理任务默认 dry-run，可 CLI 审核执行。

验收标准：
- 不删除信号追溯所需的 source_bar_ids 和 trace。
- 清理前输出影响范围。
- 无法清理时不影响 worker。

完成记录：
- 已新增 `src/stock_agent/storage/retention.py`，提供 retention plan、dry-run 审核输出和显式执行入口。
- 已新增 `stock-agent retention` 命令；默认只输出影响范围，只有传入 `--execute` 才会应用清理动作。
- `raw_bars`、`features` 旧分区会被标记为临时数据清理，`signals` 默认保留，`news` 旧分区标记为压缩，保留追溯所需 SQLite source ids 与 trace。
- 已新增 `tests/test_retention.py`，覆盖计划分类、dry-run 不修改文件、审核输出和 CLI 入口。
- 验证命令：`uv run --with pytest --with tzdata pytest tests/test_retention.py tests/test_cli_entrypoint.py`，结果 12 passed。

### T-704：发布与安装体验

状态：`done`

优先级：`P7`

依赖：T-205、T-306

任务内容：
- 补齐 `README.md`、安装命令、开发命令、测试命令。
- 支持 `pipx` 或 `uv tool` 安装说明。
- 部署模板增加 dry-run validation。

验收标准：
- 新机器按 README 可完成 demo 启动。
- 文档不包含本机绝对路径和真实密钥。
- CI 可跑无网络测试。

完成记录：
- 已补齐 `README.md`，包含 `uv sync --extra dev`、`pipx install .`、`uv tool install .`、demo 启动、常用命令、测试命令和部署 dry-run 验证。
- 已新增 `stock-agent deploy-validate`，执行离线 dry-run 检查：config、workdir、storage parent、demo CSV，不启动 worker、不访问网络。
- 已更新 `deploy/systemd`、`deploy/launchd`、`deploy/pm2` 模板及 `docs/deployment.md`，加入部署前 dry-run validation 指引。
- `pyproject.toml` 已补运行时 `tzdata` 依赖和 `dev` extra 的 `pytest`；`uv.lock` 已更新，便于 CI/新机器按锁文件恢复。
- 已修复 Windows 下测试临时 SQLite 文件锁：命令层关闭 `run_demo`、`config_review` 连接，并补齐相关测试连接关闭。
- 已新增 `tests/test_deploy_validate.py`、`tests/test_readme_installation.py`，并扩展部署模板测试与 CLI 入口测试。
- 验证命令：`uv run --extra dev pytest`，结果 269 passed, 1 xfailed。

## 12. 进阶版本推荐开发顺序

1. T-301 真实配置加载与运行时配置上下文。
2. T-302 Provider Registry 与数据源降级。
3. T-303 交易日历与盯盘窗口调度。
4. T-304 1m/tick 到 30m Bar 聚合与缺口补齐。
5. T-305 Strategy Engine 与 Signal Pipeline。
6. T-306 Worker 盯盘 Pipeline 集成。
7. T-307 通知 Outbox、合并展示与幂等发送。
8. T-308 信号追溯详情查询。
9. T-309 历史数据查询与重放。
10. T-310 端到端盯盘回归样本。
11. T-401 统一 Command Intent Schema。
12. T-402 结构化命令解析器。
13. T-406 查询服务统一化。
14. T-404 CLI 交互式 Shell。
15. T-403 LLM 自然语言解析与安全护栏。
16. T-405 真实 Telegram Bot Listener。
17. T-501 Broker Adapter 安全接口定义。
18. T-502 Broker Market Data Provider。
19. T-503 Credential 与权限安全。
20. T-504 交易动作防火墙。
21. T-601 Supervisor 独立指标复算。
22. T-602 多数据源交叉校验。
23. T-603 异常 Bar 隔离区。
24. T-604 提示文案安全审查。
25. T-605 健康指标与可观测性扩展。
26. T-701 崩溃恢复与重启预算。
27. T-702 多实例身份与锁策略。
28. T-703 数据保留与清理任务。
29. T-704 发布与安装体验。

## 13. 推荐开发顺序

1. T-001 初始化 Python 项目结构。
2. T-002 CLI 命令骨架。
3. T-003 配置文件生成与校验。
4. T-004 标准 Schema。
5. T-005 SQLite 初始化与 Repository 层。
6. T-006 固定 sample CSV 与 expected signal。
7. T-007 CSV Demo Provider。
8. T-008 Bar 加载与校验。
9. T-009 `ma_cross_demo_2_3`。
10. T-010 Trace Chain。
11. T-011 Supervisor v1。
12. T-012 Signal 持久化与通知 Sink。
13. T-013 `stock-agent run-demo`。
14. T-014 `stock-agent health`。
15. T-101 正式 `ma_cross`。
16. T-102 BOLL。
17. T-104 CLI 查询模式。
18. T-105 配置审核状态机。
19. T-106 Telegram skeleton。
20. T-107 按需新闻查询。
21. T-108 Worker 调度骨架。

## 14. 首版端到端验收清单

必须通过：
- `stock-agent init-config`
- `stock-agent run-demo`
- `stock-agent health`

必须验证：
- 无网络可运行。
- 无 Telegram token 可运行。
- 无 LLM key 可运行。
- `data/sample/sample_bars.csv` 作为唯一 demo bar 输入。
- `ma_cross_demo_2_3` 生成 deterministic expected signal。
- `signals`、`trace_chain`、`health_metrics` 写入 SQLite。
- 异常 bar 不触发普通提醒。
- 新闻模块未配置时不阻塞 demo。

## 15. 进阶版本验收清单

必须通过：
- `stock-agent init-config`
- `stock-agent run-demo`
- `stock-agent worker --once`
- `stock-agent cli signals --limit 5`
- `stock-agent cli health --verbose`
- `stock-agent cli trace <signal_id>`
- `stock-agent cli schedule`

必须验证：
- 无网络、无 Telegram token、无 LLM key 时 demo 和本地 worker 仍可运行。
- 配置文件修改后可通过 reload 生效，失败时回滚。
- worker 一轮 tick 能完成 provider、bar、strategy、supervisor、signal、notification、health 链路。
- Telegram 查询和 CLI 查询使用同一 QueryService。
- 自然语言只能生成受控 intent，不直接计算指标或修改配置。
- Telegram 变更请求必须进入 pending_review，CLI approve 后才写 YAML。
- 任一下单、撤单、转账、改账户、改密码请求都会被 TradingActionFirewall 拒绝。
- Broker API 接入默认只读，不保存 secret，不打印 account/token 明文。
- 异常 bar、指标复算不一致、主备数据偏差超阈值时不发送普通买卖观察提醒。
- 通知失败可重试，重启后 pending 通知不会丢失。
- 多策略同 timestamp 可合并通知，但 raw signals 独立存储。

## 16. 开发注意事项

- 不要把 `ma_cross_demo_2_3` 用于真实盯盘。
- 不要用 5 根 sample CSV 验证正式 `MA3/MA5`、`MA5/MA10`、`MA10/MA20` 完整行为。
- 不要用 5 根 sample CSV 验证标准 MACD。
- 指标计算、信号生成、监督复算不得调用 LLM。
- Telegram 不得直接执行配置变更。
- 所有高风险命令只能由 CLI 审核或执行。
- 新闻只做按需查询，不做晨报或启动自动推送。
- 不提交真实 API key、SQLite 运行库、DuckDB 文件、日志、`.DS_Store`。
- 不要在 P3/P4/P5 任务中实现真实自动下单；即使接入 broker，也只允许只读或行情能力。
- LLM 只能做自然语言解析、摘要或文案辅助，不能做指标计算、监督复算或无约束交易建议。
- worker 的盯盘 pipeline 优先级高于新闻和对话任务；新闻与对话必须异步或队列隔离。
- 所有进阶能力必须保留离线 demo/mock 流程，不能让测试依赖真实网络、市场开盘时间、Telegram 或 LLM。

## 17. 自然语言交互增强任务

目标：将当前交互式 CLI 从“结构化命令输入”升级为“自然语言型 agent 交互”。

新增能力：
- 用户可以输入自然语言，例如“帮我看看 QQQ 最近有没有信号”“把 QQQ 加入关注”“解释 sig-001 这条信号”。
- 系统先锁定关键字段，例如 query、symbol、limit、strategy_id、target_id、watch_window。
- 系统将字段转换成受控 CommandIntent，再生成等价 CLI 命令预览。
- 对自然语言抽取出的可执行命令，必须在执行前要求用户确认。
- 读操作确认后直接执行。
- 配置变更确认后只进入 pending_review，不直接写入配置文件。
- 高风险请求仍由 TradingActionFirewall 阻断。
- LLM 或 LangChain 只能做 intent parsing 或自然语言回答，不能直接执行策略计算、数据库写入或交易动作。

建议实现：
- 新增 `src/stock_agent/dialog/natural_language.py`：轻量规则抽取关键字段，保证无模型时可用。
- 新增 `src/stock_agent/dialog/interaction.py`：生成交互计划、命令预览、字段展示和确认策略。
- 新增 `src/stock_agent/dialog/langchain_adapter.py`：可选 LangChain adapter；未安装 LangChain 或未配置 key 时自动降级。
- 改造 `src/stock_agent/commands/interactive_cli.py`：自然语言命令先展示 plan，再要求输入 yes 确认。
- 增加测试覆盖自然语言 read-only 查询、pending change 确认、取消执行、LangChain mock parser 和高风险阻断。

验收标准：
- 无网络、无 LLM key、无 LangChain 包时，CLI 仍可通过规则抽取执行自然语言查询。
- 自然语言生成的命令在执行前必须展示 `command_preview` 和 `fields`。
- 用户未输入 `yes` 时不得执行查询或写入 pending change。
- 用户输入 `yes` 后，read-only 查询正常返回结果。
- 用户输入 `yes` 后，配置变更只进入 pending_review。
- 高风险交易、转账、读取 secret 请求不进入确认流程，直接阻断并审计。
- 现有结构化命令和 Telegram 流程不被破坏。

## 18. 本轮新增功能与完成状态

本节记录相较上一版本新增并已实现的能力，便于后续回归和继续拆分。

### T-801：Twelve Data 实时行情接入

状态：`done`

新增能力：

- 新增 Twelve Data REST Provider，通过 `TWELVE_DATA_API_KEY` 读取密钥。
- 支持 1 分钟行情拉取、超时、有限重试和每分钟请求额度保护。
- Worker 将 1 分钟数据交给现有 Bar Builder 聚合为配置周期，默认 30 分钟。
- Provider Registry 支持 Twelve Data 失败后降级到 `csv_demo`。
- 配置、部署校验、Provider 审计和 Worker 测试已覆盖新数据源。

验收标准：

- 不在配置或仓库中保存真实 API key。
- Twelve Data 可用时返回标准 Bar；异常响应给出明确错误。
- Twelve Data 不可用时按配置回退，离线 demo 与测试不依赖真实网络。

### T-802：CLI、Telegram、FastAPI 全局唯一输入权

状态：`done`

新增能力：

- 在 SQLite 中持久化唯一 `active_input`、接口在线状态和切换申请。
- CLI、Telegram、FastAPI 共用同一 `InputGate`，同一时刻只允许一个入口提交命令。
- 非当前入口返回“当前仅允许 XX 作为输入”的阻断信息，并可发起切换申请。
- 切换必须由原入口批准或拒绝；默认 10 分钟失效。
- 原入口离线时禁止创建切换申请，防止输入权在无人审批时漂移。
- 三个入口通过心跳维护在线状态，进程退出时尽量标记离线。

验收标准：

- 三个入口不能同时执行用户命令。
- 状态在进程重启后仍可从 SQLite 恢复。
- 过期申请不能批准，非原入口不能代替审批。
- 输入权控制不影响 Worker 的后台行情与策略流水线。

### T-803：FastAPI 工作台与 Telegram 长轮询

状态：`done`

新增能力：

- 新增 FastAPI 工作台、Swagger 文档、只读查询 API 和 HTML 输入控制页。
- 新增 Agent `plan` / `confirm` API、输入切换 API，以及 SSE 状态事件流。
- 新增 Telegram Bot API 长轮询 Transport，不再只保留 listener skeleton。
- Telegram 支持 `/input status|request|approve|reject`，并主动推送待审批通知。
- CLI、Telegram、FastAPI 继续复用 QueryService、配置审核与安全防火墙。

验收标准：

- FastAPI 可查询 bars、signals、trace、health 和 config changes。
- FastAPI 与 Telegram 提交命令前均通过全局输入权检查。
- Telegram token 未配置时不阻塞离线 demo。
- 网络、Telegram 和 Web 测试使用 mock/TestClient，不依赖外部服务。

### T-804：ReAct 工具路由 Agent

状态：`done`

新增能力：

- 新增中英文 ReAct Prompt Template，模型只负责工具选择和参数抽取。
- 将现有 QueryService 能力封装为 10 个带 Pydantic 参数 Schema 的只读工具。
- 新增 `ask_user`：目标工具明确但缺少参数时继续追问。
- 新增 `no_suitable_tool`：没有对应脚本或函数时明确返回不支持。
- 工具成功后直接返回确定性脚本结果，不再让模型二次改写结果。
- `tools.py` 已为每个工具增加中文用途和边界注释。

验收标准：

- Agent 不得编造工具、脚本、股票代码、行情或执行结果。
- “新增 Order Book Imbalance 信号”等未注册能力返回 `no_suitable_tool`。
- 必填参数缺失时返回追问，不得猜测参数。
- 模型不参与指标计算、信号生成、Supervisor 复算或交易动作。

### T-805：OpenRouter + Qwen 云端模型配置

状态：`done`

新增能力：

- LLM Provider 默认改为 OpenRouter，并支持自定义 OpenAI-compatible `base_url`。
- 默认模型为 `qwen/qwen3-next-80b-a3b-instruct:free`。
- 主模型遇到限流或临时服务错误时可回退到 `openrouter/free`。
- API key 统一从 `OPENROUTER_API_KEY` 环境变量读取。
- 未配置 key 或模型不可用时保留确定性解析/明确错误路径，不影响核心行情流水线。

验收标准：

- 真实 API key 不写入 `.env.example`、YAML、日志或 Git。
- 中文和英文请求均可路由到已注册工具。
- 429、502、503、504 等临时错误可触发配置的模型回退。

### T-806：本轮文档与回归测试

状态：`done`

新增内容：

- 新增当前系统 DAG、输入权控制说明和 Agent Prompt/Tool Calling 评审文档。
- 增加 Agent、输入入口、FastAPI、Telegram Transport、配置和 Worker 回归测试。
- 本轮完整测试结果：`305 passed, 1 xfailed, 84 subtests passed`。

当前边界：

- 系统仍是行情观察与信号提醒平台，不自动下单。
- Agent 只能调用注册工具，尚不能动态创建新策略或任意执行 Python。
- 最终买卖信号向所有已激活交互界面的统一广播仍属于后续任务。
- `src/stock_agent/commands/web.py` 已提供启动实现，但 `stock-agent web` CLI 子命令尚未注册。

### T-807：单标的动态查询时间约束

状态：`done`

新增能力：

- 查询具体股票或指数的行情、K 线或信号时，必须提供开始时间、结束时间和明确的 IANA 时区。
- 开始和结束时间必须包含完整日期与时分，结束时间必须晚于开始时间。
- “今天”“最近”“开盘后”等相对时间不能由模型自行猜测，缺少信息时统一返回追问。
- 时间按用户指定时区解析，并在进入 QueryService 前归一化为 UTC。
- Prompt、Tool 参数 Schema、CommandIntent 和运行时执行边界均执行相同约束。
- 全局信号列表、健康状态、交易日程等非单标的查询不受该规则影响。

验收结果：

- 缺少时间范围或时区的股票/指数动态查询返回 `ClarificationIntent` 或 `needs_user_input`。
- 无效时区、只有日期没有具体时间、结束时间早于开始时间均被拒绝。
- CLI、Telegram、FastAPI/ReAct Agent 使用一致的时间约束。
- 完整测试结果：`311 passed, 1 xfailed`。

### T-808：Agent 直接查询 Twelve Data 行情

状态：`done`

新增能力：

- 新增只读工具 `fetch_twelve_data_bars`，复用现有 Twelve Data REST Provider。
- Agent 可根据中英文自然语言提取股票/指数、起止时间、IANA 时区、K 线周期和返回数量。
- 工具直接请求 Twelve Data，不依赖 Worker 定时抓取，也不读取本地 Data Lake。
- 返回标准化 OHLCV Bar，不运行策略、不生成信号、不修改配置或数据库。
- 缺少股票、起止时间或时区时进入追问；Provider 不可用时返回受控错误。

技术决策：

- 当前不引入 MCP。Agent 与 Provider 位于同一 Python 进程，直接注册 Tool 的链路更短。
- 若未来需要向 VS Code、Codex 或其他外部 Agent 暴露工具，再增加 MCP Server 适配层。
- 完整测试结果：`313 passed, 1 xfailed`。

自然语言示例：

```text
请直接从 Twelve Data 获取 QQQ 在 2026-07-06 09:30 到
2026-07-06 10:30 America/New_York 的 1 分钟行情
```
