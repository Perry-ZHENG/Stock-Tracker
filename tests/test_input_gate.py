import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.dialog.input_gate import InputGate, InputGateError
from stock_agent.storage.sqlite import initialize_runtime_database


class InputGateTests(unittest.TestCase):
    def test_first_valid_source_claims_input_and_other_source_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            gate = InputGate(connection)

            first = gate.check("cli", actor_ref="cli-session")
            blocked = gate.check("fastapi", actor_ref="web-session")
            state = gate.state()
            connection.close()

        self.assertTrue(first.allowed)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.active_source, "cli")
        self.assertEqual(state.active_source, "cli")

    def test_original_source_must_approve_before_control_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            gate = InputGate(connection)
            gate.check("cli", actor_ref="cli-session")
            gate.heartbeat("fastapi", actor_ref="web-session")

            request = gate.request_switch("fastapi", actor_ref="web-session")
            before = gate.check("fastapi", actor_ref="web-session")
            approved = gate.decide(
                request.request_id,
                source="cli",
                actor_ref="cli-session",
                approve=True,
            )
            after = gate.check("fastapi", actor_ref="web-session")
            connection.close()

        self.assertEqual(request.status, "pending")
        self.assertFalse(before.allowed)
        self.assertEqual(approved.status, "approved")
        self.assertTrue(after.allowed)

    def test_non_active_source_cannot_approve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            gate = InputGate(connection)
            gate.check("cli", actor_ref="cli-session")
            request = gate.request_switch("fastapi", actor_ref="web-session")

            with self.assertRaisesRegex(InputGateError, "原输入接口"):
                gate.decide(
                    request.request_id,
                    source="fastapi",
                    actor_ref="web-session",
                    approve=True,
                )
            connection.close()

    def test_switch_cannot_be_requested_while_active_source_is_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            gate = InputGate(connection)
            gate.check("cli", actor_ref="cli-session")
            gate.mark_offline("cli", actor_ref="cli-session")

            with self.assertRaisesRegex(InputGateError, "离线"):
                gate.request_switch("telegram", actor_ref="telegram-bot")
            connection.close()

    def test_pending_request_expires_after_ten_minutes(self) -> None:
        now = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)

        def clock() -> datetime:
            return now

        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            gate = InputGate(connection, now_fn=clock)
            gate.check("cli", actor_ref="cli-session")
            request = gate.request_switch("fastapi", actor_ref="web-session")

            now += timedelta(minutes=10, seconds=1)
            expired = gate.get_request(request.request_id)
            connection.close()

        self.assertIsNotNone(expired)
        self.assertEqual(expired.status, "expired")


if __name__ == "__main__":
    unittest.main()
