import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.query_cli import run_cli_query
from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.scheduler import USMarketCalendar, build_watch_schedule


class MarketScheduleTests(unittest.TestCase):
    def test_regular_trading_day_builds_watch_windows_in_utc(self) -> None:
        config = validate_config(DEFAULT_CONFIG)

        schedule = build_watch_schedule(
            config=config.schedule,
            target_date=date(2026, 5, 22),
        )

        self.assertTrue(schedule.market_day.is_trading_day)
        self.assertFalse(schedule.market_day.is_half_day)
        self.assertEqual([window.kind for window in schedule.windows], ["premarket", "regular", "close_focus", "afterhours"])
        self.assertFalse(schedule.windows[0].strategy_enabled)
        self.assertTrue(schedule.windows[1].strategy_enabled)
        self.assertTrue(schedule.windows[2].strategy_enabled)
        self.assertFalse(schedule.windows[3].strategy_enabled)
        self.assertEqual(schedule.windows[1].start_utc.hour, 13)
        self.assertEqual(schedule.windows[1].start_utc.minute, 30)
        self.assertEqual(schedule.windows[1].end_utc.hour, 20)
        self.assertEqual(schedule.windows[1].end_utc.minute, 0)

    def test_weekend_returns_closed_and_next_trading_day(self) -> None:
        config = validate_config(DEFAULT_CONFIG)

        schedule = build_watch_schedule(
            config=config.schedule,
            target_date=date(2026, 5, 23),
        )

        self.assertFalse(schedule.market_day.is_trading_day)
        self.assertEqual(schedule.windows[0].kind, "closed")
        self.assertEqual(schedule.next_trading_day.date, date(2026, 5, 26))

    def test_holiday_returns_closed(self) -> None:
        market_day = USMarketCalendar().market_day(date(2026, 6, 19))

        self.assertFalse(market_day.is_trading_day)
        self.assertTrue(market_day.is_holiday)
        self.assertEqual(market_day.holiday_name, "Juneteenth National Independence Day")

    def test_half_day_closes_at_one_pm_eastern(self) -> None:
        market_day = USMarketCalendar().market_day(date(2026, 7, 2))

        self.assertTrue(market_day.is_trading_day)
        self.assertTrue(market_day.is_half_day)
        self.assertEqual(market_day.close_at.hour, 13)
        self.assertEqual(market_day.holiday_name, "Independence Day early close")

    def test_cli_schedule_does_not_require_runtime_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            stream = io.StringIO()

            exit_code = run_cli_query(
                root,
                query="schedule",
                schedule_date=date(2026, 5, 22),
                stream=stream,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("schedule_date=2026-05-22", stream.getvalue())
        self.assertIn("regular", stream.getvalue())
        self.assertIn("strategy_enabled", stream.getvalue())

    def test_stock_agent_cli_schedule_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            with patch("pathlib.Path.cwd", return_value=root):
                self.assertEqual(main(["cli", "schedule"]), 0)


if __name__ == "__main__":
    unittest.main()
