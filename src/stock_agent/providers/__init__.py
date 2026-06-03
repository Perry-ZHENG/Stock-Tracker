"""Market data provider adapters."""

from stock_agent.providers.csv_demo import CsvDemoProvider, CsvDemoProviderError

__all__ = ["CsvDemoProvider", "CsvDemoProviderError"]
