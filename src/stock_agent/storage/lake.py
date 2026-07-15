"""File lake writers for replayable offline data."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from stock_agent.schemas import Bar, NewsItem
from stock_agent.security import redact_sensitive

LakeDataset = Literal["raw_bars", "features", "news"]


@dataclass(frozen=True)
class LakeWriteResult:
    dataset: LakeDataset
    path: Path
    format: Literal["jsonl", "parquet"]
    rows: int


class LakeWriter:
    """Write replayable data into date-partitioned lake paths.

    Parquet is preferred, with JSONL fallback when no local Parquet engine is installed.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def write_raw_bars(self, bars: list[Bar]) -> LakeWriteResult:
        return self._write_models("raw_bars", bars, partition_date=_date_from_models(bars, "timestamp"))

    def write_news(self, news_items: list[NewsItem]) -> LakeWriteResult:
        return self._write_models("news", news_items, partition_date=_date_from_models(news_items, "published_at"))

    def write_features(
        self,
        records: list[dict[str, Any]],
        *,
        partition_date: date | None = None,
    ) -> LakeWriteResult:
        resolved_date = partition_date or _date_from_dicts(records, "timestamp")
        return self._write_records("features", records, partition_date=resolved_date)

    def _write_models(
        self,
        dataset: LakeDataset,
        models: list[BaseModel],
        *,
        partition_date: date,
    ) -> LakeWriteResult:
        records = [redact_sensitive(model.model_dump(mode="json")) for model in models]
        return self._write_records(dataset, records, partition_date=partition_date)

    def _write_records(
        self,
        dataset: LakeDataset,
        records: list[dict[str, Any]],
        *,
        partition_date: date,
    ) -> LakeWriteResult:
        partition_dir = self.root / dataset / f"date={partition_date.isoformat()}"
        partition_dir.mkdir(parents=True, exist_ok=True)

        if _parquet_available():
            return _write_parquet(dataset, partition_dir, [redact_sensitive(record) for record in records])
        return _write_jsonl(dataset, partition_dir, [redact_sensitive(record) for record in records])


def _write_jsonl(
    dataset: LakeDataset,
    partition_dir: Path,
    records: list[dict[str, Any]],
) -> LakeWriteResult:
    path = partition_dir / "part-00000.jsonl"
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            file.write("\n")
    return LakeWriteResult(dataset=dataset, path=path, format="jsonl", rows=len(records))


def _write_parquet(
    dataset: LakeDataset,
    partition_dir: Path,
    records: list[dict[str, Any]],
) -> LakeWriteResult:
    # This branch is intentionally isolated so environments without pyarrow keep working.
    import pandas as pd

    path = partition_dir / "part-00000.parquet"
    pd.DataFrame.from_records(records).to_parquet(path, index=False)
    return LakeWriteResult(dataset=dataset, path=path, format="parquet", rows=len(records))


def _parquet_available() -> bool:
    return importlib.util.find_spec("pyarrow") is not None or importlib.util.find_spec("fastparquet") is not None


def _date_from_models(models: list[BaseModel], field_name: str) -> date:
    if not models:
        return date.today()
    value = getattr(models[0], field_name)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(f"{field_name} must be date-like")


def _date_from_dicts(records: list[dict[str, Any]], field_name: str) -> date:
    if not records:
        return date.today()
    value = records[0].get(field_name)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    raise ValueError(f"{field_name} must be date-like")


__all__ = ["LakeWriteResult", "LakeWriter"]
