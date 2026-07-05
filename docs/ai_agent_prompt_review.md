# AI Agent Prompt 与 Tool Calling 草案（待审核）

状态：ReAct Agent 已接入 FastAPI Agent 路径；本文件记录 Prompt、工具边界和参数协议。

## 目标

Agent 只负责：

1. 理解中文或英文输入；
2. 从注册工具中选择一个工具；
3. 提取并校验工具参数；
4. 缺参时继续询问；
5. 无工具时明确返回不支持；
6. 工具执行成功后由系统直接返回脚本结果，不再调用模型二次总结。

Agent 不负责自由生成或执行 Python，不允许绕过工具注册表。

## 输出协议

调用查询工具：

```text
Thought: 用户希望查询 QQQ 的 MACD 信号。
Action: query_signals[{"symbol":"QQQ","strategy_id":"macd","from_ts":"2026-07-06 09:30","to_ts":"2026-07-06 16:00","timezone":"America/New_York","limit":10}]
```

参数不足：

```text
Thought: 查询 K 线需要明确股票代码。
Action: ask_user[{"question":"你希望查询哪个股票代码？","missing":["symbol"]}]
```

没有合适工具：

```text
Thought: 当前没有创建新策略的注册工具。
Action: no_suitable_tool[{"reason":"当前没有新增 Order Book Imbalance 策略的工具"}]
```

无需调用工具、直接完成说明时：

```text
Thought: 用户的问题无需运行工具。
Action: Finish[最终说明。]
```

## 第一批工具

| 工具 | 中文备注 | 对应现有脚本/模块 | 关键参数 | 调用示例 |
|---|---|---|---|---|
| `query_signals` | 查询系统已经计算并保存的观察信号，可按股票、策略和时间范围过滤；不能创建新策略 | `QueryService.execute("signals")` | 指定 `symbol` 时必填 `from_ts`、`to_ts`、`timezone` | `query_signals[{"symbol":"QQQ","from_ts":"2026-07-06 09:30","to_ts":"2026-07-06 16:00","timezone":"America/New_York"}]` |
| `query_bars` | 查询本地 Data Lake 中某只股票或指数的历史 K 线 | `QueryService.execute("bars")` | 必填 `symbol`、`from_ts`、`to_ts`、`timezone` | `query_bars[{"symbol":"QQQ","from_ts":"2026-07-06 09:30","to_ts":"2026-07-06 16:00","timezone":"America/New_York"}]` |
| `fetch_twelve_data_bars` | 直接请求 Twelve Data 的远程 OHLCV，不读取本地缓存、不运行策略 | `TwelveDataProvider.fetch_intraday_bars()` | 必填 `symbol`、`from_ts`、`to_ts`、`timezone`；可选 `interval`、`limit` | `fetch_twelve_data_bars[{"symbol":"QQQ","from_ts":"2026-07-06 09:30","to_ts":"2026-07-06 10:30","timezone":"America/New_York","interval":"1m"}]` |
| `query_health` | 查看 Worker、行情 Provider、Supervisor 等模块是否健康 | `QueryService.execute("health")` | 可选 `limit` | `query_health[{"limit":10}]` |
| `query_trace` | 解释某个信号从行情、策略计算到审核的追踪链 | `QueryService.execute("trace")` | 必填 `target_id`，可以是 signal_id 或 trace_id | `query_trace[{"target_id":"sig-001"}]` |
| `query_news` | 查询市场或指定股票的新闻；当前新闻 Provider 可能尚未配置 | `QueryService.execute("news")` | 可选 `symbol`、`limit` | `query_news[{"symbol":"AAPL","limit":5}]` |
| `query_statistics` | 查看按日、月或年汇总的信号统计 | `QueryService.execute("stats")` | `period`：`day`、`month` 或 `year` | `query_statistics[{"period":"month","limit":10}]` |
| `query_schedule` | 查询交易日、休市情况和当前监控时间窗口 | `QueryService.execute("schedule")` | 无 | `query_schedule[{}]` |
| `query_provider_compare` | 查看不同行情源之间的数据质量比较和差异记录 | `QueryService.execute("provider-compare")` | 可选 `limit` | `query_provider_compare[{"limit":10}]` |
| `query_abnormal_bars` | 查询因价格、成交量或格式异常而被隔离的行情 Bar | `QueryService.execute("abnormal-bars")` | 可选 `limit` | `query_abnormal_bars[{"limit":20}]` |
| `query_config_changes` | 查看待审核、已批准或已拒绝的配置修改记录 | `QueryService.execute("config-changes")` | 可选 `limit` | `query_config_changes[{"limit":10}]` |
| `ask_user` | 当目标工具明确，但缺少必填参数或参数有歧义时继续向用户提问；不会运行行情脚本 | Agent 对话控制 | 必填 `question`；可选 `missing` | `ask_user[{"question":"请提供股票代码","missing":["symbol"]}]` |
| `no_suitable_tool` | 当前没有任何注册工具能完成请求时明确结束路由；不会选择相似工具凑数 | Agent 对话控制 | 必填 `reason` | `no_suitable_tool[{"reason":"当前没有创建新策略的工具"}]` |

### 工具使用边界备注

- `query_*` 工具均为只读工具，不修改配置，也不触发交易。
- `query_signals` 读取的是已经由 Worker 生成的信号，不会临时创建信号。
- `query_bars` 缺少 `symbol` 时，Agent 必须调用 `ask_user`，不能猜测。
- 用户明确要求 Twelve Data、实时行情或最新远程行情时，调用
  `fetch_twelve_data_bars`；`query_bars` 只读取本地 Data Lake。
- 查询具体股票或指数的行情、K 线或信号时，必须同时提供开始时间、结束时间和
  IANA 时区；时间必须精确到时分。缺少任一项时调用 `ask_user`，不得把“今天”
  “最近”等相对时间静默转换为模型猜测的时间。
- `ask_user` 用于“有合适工具但缺参数”的情况。
- `no_suitable_tool` 用于“系统根本没有对应工具”的情况。
- “新增 Order Book Imbalance 信号”当前应调用 `no_suitable_tool`。
- “查询 QQQ 今天的 MACD 信号”缺少具体起止时间和时区，应先调用 `ask_user`。

当前 Agent Tool 为进程内 Python 工具，不使用 MCP。若未来需要让 VS Code、Codex
或其他外部 Agent 客户端发现并调用这些工具，可再增加 MCP Server 适配层。

首版没有注册下单、资金、账户、密钥读取、任意 Python、任意 shell、创建新策略、
启动常驻服务等工具。

## 需要审核的决定

1. 是否保留 `Thought:` 字段。当前限定为一句决策摘要，不允许详细思维链。
2. `query_signals` 是否允许 `symbol` 为空，从而查询所有股票。
3. 新增或启停策略是否进入第二批工具，并强制人工确认。
4. Agent 无合适工具时，是返回固定文本，还是允许解释缺少什么能力。
5. Tool Observation 最终是否原样返回，还是让模型生成简洁的自然语言总结。
