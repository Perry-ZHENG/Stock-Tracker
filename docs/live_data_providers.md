# Live Market Data Provider Evaluation

T-201 evaluates live provider options and implements the first replaceable adapter. The strategy layer must consume only standard `Bar` schema objects and must never depend on vendor response keys.

## Current Adapter Decision

首个实现 adapter：`alpha_vantage`

原因：
- 官方 `TIME_SERIES_INTRADAY` endpoint 支持 `1min`、`5min`、`15min`、`30min`、`60min` OHLCV。
- 参数支持 `adjusted=true` 与 `extended_hours=false`，和本项目首版“前复权、常规交易时段策略计算”的口径更接近。
- JSON 响应结构简单，适合先做可测试 adapter。

限制：
- 实时/延迟/历史 intraday 的可用性与 entitlement/套餐有关。
- provider 返回限频或信息消息时，adapter 必须抛出 `LiveProviderError`，由调用方降级到 demo/cache。

## Provider Notes

| Provider | Fit | Notes |
| --- | --- | --- |
| Alpha Vantage | First adapter | Intraday OHLCV endpoint, supports 30min interval and adjusted/regular-hours parameters. |
| Twelve Data | Candidate | Unified market data API with time series endpoint. Good future candidate if quota/pricing fits. |
| Polygon.io | Candidate | Strong US stock aggregates/bars coverage, but plan/entitlement and market-data licensing need explicit review before default use. |
| marketstack | Candidate with caveat | Intraday endpoint exists; realtime minute updates require higher plan according to docs. |
| Nasdaq Data Link | Enterprise candidate | Bars endpoint exists for US-listed/OTCBB securities, but subscription/product setup is heavier. |

## Official References

- Alpha Vantage documentation: https://www.alphavantage.co/documentation/
- Twelve Data documentation: https://twelvedata.com/docs/introduction
- Polygon stocks REST overview: https://polygon.io/docs/rest/stocks/overview
- marketstack documentation: https://marketstack.com/documentation
- Nasdaq Data Link API overview: https://www.nasdaq.com/solutions/data/nasdaq-data-link/api
