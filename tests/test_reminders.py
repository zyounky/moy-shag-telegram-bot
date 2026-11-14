from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from assistant_bot.reminders import ReminderStore, resolve_local_time


class RemindersTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = Path(temp.name) / "reminders.sqlite3"
        self.store = ReminderStore(self.database)
        self.start = datetime(2026, 11, 14, 12, tzinfo=UTC)
        self.due_at = self.start + timedelta(minutes=10)

    def create(self, frequency="once"):
        return self.store.create(
            123,
            "Прочитать статью",
            self.due_at,
            "Europe/Moscow",
            frequency,
            action_label="Открыть материал",
            action_url="https://example.org/material",
            now=self.start,
        )

    def test_one_off_survives_restart_and_is_acknowledged_once(self):
        original = self.create()
        restarted = ReminderStore(self.database)
        self.assertEqual(restarted.list_active(123), [original])
        self.assertEqual(restarted.due(now=self.start), [])

        due, = restarted.due(now=self.due_at)
        self.assertEqual(due.action_url, "https://example.org/material")
        self.assertEqual(self.store.due(now=self.due_at), [])
        self.assertTrue(restarted.acknowledge(due, at=self.due_at))
        self.assertFalse(restarted.acknowledge(due, at=self.due_at))
        self.assertEqual(restarted.list_active(123), [])
        self.assertFalse(restarted.get(123, original.id).active)

    def test_frequency_change_and_disable_are_chat_scoped(self):
        created = self.create("daily")
        sent, = self.store.due(now=self.due_at)
        self.assertTrue(self.store.acknowledge(sent, at=self.due_at))
        after_first = self.due_at + timedelta(seconds=1)
        self.assertIsNone(self.store.get(456, created.id))
        self.assertIsNone(self.store.set_frequency(456, created.id, "weekly", now=after_first))
        self.assertFalse(self.store.disable(456, created.id))

        edited = self.store.set_frequency(123, created.id, "weekly", now=after_first)
        self.assertEqual(edited.frequency, "weekly")
        self.assertEqual(edited.next_run, self.due_at + timedelta(weeks=1))
        self.assertTrue(self.store.disable(123, created.id))
        self.assertEqual(self.store.due(now=edited.next_run), [])

    def test_retries_and_crash_recovery_lease_only_one_occurrence(self):
        self.create()
        first, = self.store.due(now=self.due_at)
        self.assertTrue(self.store.retry(first, at=self.due_at))
        self.assertEqual(self.store.due(now=self.due_at), [])
        retry_at = self.due_at + timedelta(seconds=30)
        second, = self.store.due(now=retry_at)
        self.assertFalse(self.store.acknowledge(first, at=retry_at))

        restarted = ReminderStore(self.database)
        self.assertEqual(restarted.due(now=retry_at + timedelta(minutes=4)), [])
        recovery_at = retry_at + timedelta(minutes=5)
        recovered, = restarted.due(now=recovery_at)
        self.assertFalse(self.store.acknowledge(second, at=recovery_at))
        self.assertTrue(restarted.acknowledge(recovered, at=recovery_at))

    def test_recurrence_holds_local_hour_through_dst_and_skips_missed_deliveries(self):
        zone = ZoneInfo("Europe/Berlin")
        previous = datetime(2026, 3, 27, 8, tzinfo=UTC)
        created = self.store.create(
            123,
            "Проверить задачи",
            datetime(2026, 3, 28, 9),
            zone.key,
            "daily",
            now=previous,
        )
        late = datetime(2026, 3, 30, 9, tzinfo=UTC)
        claim, = self.store.due(now=late)
        self.assertEqual(claim.next_run, datetime(2026, 3, 28, 8, tzinfo=UTC))
        self.assertTrue(self.store.acknowledge(claim, at=late))
        next_run = self.store.get(123, created.id).next_run
        self.assertEqual(next_run, datetime(2026, 3, 31, 7, tzinfo=UTC))
        self.assertEqual(next_run.astimezone(zone).hour, 9)

    def test_invalid_wall_time_and_past_schedule_are_rejected(self):
        with self.assertRaises(ValueError):
            resolve_local_time(datetime(2026, 3, 29, 2, 30), "Europe/Berlin")
        ambiguous = resolve_local_time(datetime(2026, 10, 25, 2, 30), "Europe/Berlin")
        self.assertEqual(ambiguous.astimezone(UTC), datetime(2026, 10, 25, 0, 30, tzinfo=UTC))
        with self.assertRaises(ValueError):
            self.store.create(123, "Просрочено", self.start, "Europe/Moscow", now=self.start)

    def test_editing_frequency_does_not_cancel_a_reserved_occurrence(self):
        created = self.create("daily")
        claim, = self.store.due(now=self.due_at)
        updated = self.store.set_frequency(123, created.id, "weekly", now=self.due_at)
        self.assertEqual(updated.next_run, self.due_at)
        self.assertTrue(self.store.acknowledge(claim, at=self.due_at))
        self.assertEqual(
            self.store.get(123, created.id).next_run, self.due_at + timedelta(weeks=1)
        )


if __name__ == "__main__":
    unittest.main()
