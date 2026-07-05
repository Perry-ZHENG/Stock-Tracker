# 单一输入入口控制

系统在 CLI、Telegram、FastAPI 三个命令入口之间维护唯一的 `active_input`。
入口状态、在线心跳和切换申请保存在运行时 SQLite 中，重启后不会丢失。

## 状态流

```text
新入口提交命令
  -> 当前入口相同：允许执行
  -> 当前入口不同：拒绝执行并提示申请切换
  -> 新入口创建 pending 申请
  -> 原入口 approve / reject
  -> approve 后 active_input 指向新入口
  -> 10 分钟未审批自动 expired
```

首次出现的合法命令入口会取得输入权。原入口离线时不能创建切换申请。
原始命令不会排队，也不会在切换成功后自动重放。

## CLI

交互模式启动后会保持在线心跳：

```powershell
stock-agent cli
```

CLI 不是当前入口时，输入任意命令后可按提示输入 `yes` 创建切换申请。
CLI 是原入口时使用以下命令审批：

```text
approve switch-xxxxxxxxxxxx
reject switch-xxxxxxxxxxxx
```

一次性 `stock-agent cli signals` 等命令也受输入门控约束。

## Telegram

非当前入口发送命令时，Bot 返回切换提示：

```text
/input request
```

Telegram 是原入口时，Bot 会主动发送审批消息：

```text
/input approve switch-xxxxxxxxxxxx
/input reject switch-xxxxxxxxxxxx
```

还可查询状态：

```text
/input status
```

`stock-agent telegram` 使用 Telegram Bot API long polling，并定期更新入口在线心跳。

## FastAPI

浏览器访问首页可查看当前入口、提交切换申请以及批准或拒绝请求。
页面通过 SSE 接收输入状态变化，并以 HTTP heartbeat 保持在线状态。

主要接口：

```text
GET  /api/v1/input
POST /api/v1/input/heartbeat
POST /api/v1/input/switch/requests
POST /api/v1/input/switch/requests/{request_id}/approve
POST /api/v1/input/switch/requests/{request_id}/reject
GET  /api/v1/events
```

## 默认配置

```yaml
input_control:
  request_ttl_sec: 600
  cli_online_timeout_sec: 45
  fastapi_online_timeout_sec: 45
  telegram_online_timeout_sec: 120
```

这些设置分别控制切换申请有效期，以及各入口心跳多久未更新后被视为离线。

本功能只控制命令输入归属，不改变买卖观察信号的通知渠道。
