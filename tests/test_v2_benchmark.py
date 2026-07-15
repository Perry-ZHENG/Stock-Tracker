from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_agent.evaluation import (
    BenchmarkRunner,
    load_benchmark_baseline,
    load_benchmark_cases,
    render_benchmark_markdown,
    write_benchmark_report,
)
from stock_agent.evaluation.metrics import (
    compare_baseline,
    evidence_coverage,
    evidence_precision,
    numeric_consistency,
    rate,
    unsupported_claim_rate,
)


FIXTURES = Path(__file__).parent / "fixtures" / "v2_benchmark"
FIXED_CLOCK = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


def test_metrics_handle_empty_and_regressed_inputs() -> None:
    assert rate([]) is None
    assert evidence_precision([], ["evidence-1"]) is None
    assert evidence_coverage(["evidence-1"], []) is None
    assert numeric_consistency([]) is None
    assert unsupported_claim_rate([], []) == 0
    assert compare_baseline({"quality": 0.9}, {"quality": 1.0}) == ["baseline_regression:quality"]
    assert compare_baseline({}, {"quality": 1.0}) == ["baseline_missing:quality"]


def test_small_offline_benchmark_is_deterministic_and_meets_baseline(tmp_path: Path) -> None:
    cases = load_benchmark_cases(FIXTURES / "small_cases.json", tier="small")
    baseline = load_benchmark_baseline(FIXTURES / "baseline.json")
    runner = BenchmarkRunner(fixed_clock=FIXED_CLOCK)

    first = runner.run(cases, baseline=baseline)
    second = runner.run(cases, baseline=baseline)
    markdown = render_benchmark_markdown(first)
    json_path = tmp_path / "benchmark.json"
    markdown_path = tmp_path / "benchmark.md"
    write_benchmark_report(first, json_path=json_path, markdown_path=markdown_path)

    assert first.passed
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert {case.case_id for case in first.cases} >= {
        "facts-grounded",
        "anomaly-context",
        "macro-mcp-bounded",
        "signal-leakage-rejected",
        "sandbox-attack-rejected",
        "conflict-and-no-evidence",
    }
    assert "scripted_model_responses" not in first.model_dump_json()
    assert "# V2 Offline Benchmark" in markdown
    assert "status=passed" in markdown
    assert "baseline-2026-07-15" in json_path.read_text(encoding="utf-8")
    assert "scripted_model_responses" not in markdown_path.read_text(encoding="utf-8")


@pytest.mark.full_benchmark
def test_full_offline_benchmark_requires_explicit_opt_in() -> None:
    if os.getenv("RUN_FULL_BENCHMARK") != "1":
        pytest.skip("set RUN_FULL_BENCHMARK=1 to run the full offline benchmark")
    cases = [
        *load_benchmark_cases(FIXTURES / "small_cases.json", tier="small"),
        *load_benchmark_cases(FIXTURES / "full_cases.json", tier="full"),
    ]
    report = BenchmarkRunner(fixed_clock=FIXED_CLOCK).run(cases)

    assert report.passed
