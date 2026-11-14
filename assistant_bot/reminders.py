"""Persistent Telegram reminder schedules and delivery reservations."""

from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo


FREQUENCIES = ("once", "daily", "weekly")
_REPEAT_DAYS = {"daily": 1, "weekly": 7}


@dataclass(frozen=True)
class Reminder:
    id: int
    chat_id: int
    title: str
    next_run: datetime
    timezone: str
    frequency: str
    active: bool
    action_label: str | None
    action_url: str | None
    _lease_token: str | None = field(default=None, repr=False, compare=False)


def resolve_local_time(local_time: datetime, timezone: str) -> datetime:
    """Validate an entered wall time, using the earlier time on a DST overlap."""
    if local_time.tzinfo is not None:
        raise ValueError("Expected a local time without a time zone.")
    local_time = local_time.replace(fold=0)
    zoned = local_time.replace(tzinfo=ZoneInfo(timezone))
    if _round_trip(zoned).replace(tzinfo=None) != local_time:
        raise ValueError("This local time is skipped during a time zone transition.")
    return zoned


def _round_trip(zoned: datetime) -> datetime:
    return zoned.astimezone(UTC).astimezone(zoned.tzinfo)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.utcoffset() is None:
        raise ValueError("The reference time must include a time zone.")
    return value.astimezone(UTC)


def _next_recurrence(row: sqlite3.Row, frequency: str, after: datetime) -> datetime:
    anchor = datetime.fromisoformat(row["anchor_local"])
    zone = ZoneInfo(row["timezone"])
    step_days = _REPEAT_DAYS[frequency]
    elapsed_days = (after.astimezone(zone).date() - anchor.date()).days
    whole_intervals = max(0, elapsed_days // step_days)
    wall_time = anchor + timedelta(days=whole_intervals * step_days)
    # Move a skipped wall time forward by the DST gap for this occurrence only.
    candidate = _round_trip(wall_time.replace(tzinfo=zone)).astimezone(UTC)
    if candidate <= after:
        wall_time += timedelta(days=step_days)
        candidate = _round_trip(wall_time.replace(tzinfo=zone)).astimezone(UTC)
    return candidate


def _as_reminder(row: sqlite3.Row, *, lease_token: str | None = None) -> Reminder:
    return Reminder(
        id=row["id"],
        chat_id=row["chat_id"],
        title=row["title"],
        next_run=datetime.fromtimestamp(row["next_run"], UTC),
        timezone=row["timezone"],
        frequency=row["frequency"],
        active=bool(row["active"]),
        action_label=row["action_label"],
        action_url=row["action_url"],
        _lease_token=lease_token,
    )


class ReminderStore:
    """Store schedules in a SQLite file shared by the bot and its worker."""

    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    next_run REAL NOT NULL,
                    anchor_local TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    frequency TEXT NOT NULL CHECK (frequency IN ('once', 'daily', 'weekly')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    action_label TEXT,
                    action_url TEXT,
                    lease_token TEXT,
                    lease_until REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS reminders_due
                    ON reminders(next_run, id) WHERE active = 1;
                CREATE INDEX IF NOT EXISTS reminders_chat
                    ON reminders(chat_id, next_run, id) WHERE active = 1;
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def create(
        self,
        chat_id: int,
        title: str,
        scheduled_for: datetime,
        timezone: str,
        frequency: str = "once",
        *,
        action_label: str | None = None,
        action_url: str | None = None,
        now: datetime | None = None,
    ) -> Reminder:
        if frequency not in FREQUENCIES:
            raise ValueError("Unsupported reminder frequency.")
        title = title.strip()
        if not title:
            raise ValueError("Add a title for the reminder.")
        zone = ZoneInfo(timezone)
        if scheduled_for.tzinfo is None:
            scheduled_for = resolve_local_time(scheduled_for, timezone)
        starts_at = _utc(scheduled_for)
        if starts_at <= _utc(now):
            raise ValueError("The reminder must be scheduled in the future.")
        anchor = starts_at.astimezone(zone).replace(tzinfo=None).isoformat()
        with closing(self._connect()) as connection, connection:
            insert = connection.execute(
                """INSERT INTO reminders (
                       chat_id, title, next_run, anchor_local, timezone, frequency,
                       action_label, action_url
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    chat_id, title, starts_at.timestamp(), anchor, timezone, frequency,
                    action_label, action_url,
                ),
            )
            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ?", (insert.lastrowid,)
            ).fetchone()
            return _as_reminder(row)

    def list_active(self, chat_id: int) -> list[Reminder]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT * FROM reminders WHERE chat_id = ? AND active = 1
                   ORDER BY next_run, id""",
                (chat_id,),
            ).fetchall()
            return [_as_reminder(row) for row in rows]

    def get(self, chat_id: int, reminder_id: int) -> Reminder | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE chat_id = ? AND id = ?",
                (chat_id, reminder_id),
            ).fetchone()
            if row is None:
                return None
            return _as_reminder(row)

    def set_frequency(
        self,
        chat_id: int,
        reminder_id: int,
        frequency: str,
        *,
        now: datetime | None = None,
    ) -> Reminder | None:
        if frequency not in FREQUENCIES:
            raise ValueError("Unsupported reminder frequency.")
        current = _utc(now)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM reminders WHERE chat_id = ? AND id = ? AND active = 1",
                (chat_id, reminder_id),
            ).fetchone()
            if row is None:
                return None
            reminder = _as_reminder(row)
            next_run = reminder.next_run
            # Preserve the first delivery and any occurrence already due or being sent.
            anchor_date = datetime.fromisoformat(row["anchor_local"]).date()
            next_date = next_run.astimezone(ZoneInfo(reminder.timezone)).date()
            if (
                frequency != reminder.frequency
                and frequency != "once"
                and next_date > anchor_date
                and next_run > current
            ):
                next_run = _next_recurrence(row, frequency, current)
            connection.execute(
                "UPDATE reminders SET frequency = ?, next_run = ? WHERE chat_id = ? AND id = ?",
                (frequency, next_run.timestamp(), chat_id, reminder_id),
            )
            return replace(reminder, frequency=frequency, next_run=next_run)

    def disable(self, chat_id: int, reminder_id: int) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE reminders SET active = 0, lease_token = NULL, lease_until = 0
                   WHERE chat_id = ? AND id = ? AND active = 1""",
                (chat_id, reminder_id),
            )
            return cursor.rowcount == 1

    def due(self, *, now: datetime | None = None, limit: int = 100) -> list[Reminder]:
        """Reserve due occurrences for five minutes, across process restarts."""
        if limit <= 0:
            raise ValueError("The delivery limit must be positive.")
        current = _utc(now).timestamp()
        token = uuid4().hex
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM reminders
                   WHERE active = 1 AND next_run <= ? AND lease_until <= ?
                   ORDER BY next_run, id LIMIT ?""",
                (current, current, limit),
            ).fetchall()
            connection.executemany(
                "UPDATE reminders SET lease_token = ?, lease_until = ? WHERE id = ?",
                ((token, current + 300, row["id"]) for row in rows),
            )
            return [_as_reminder(row, lease_token=token) for row in rows]

    def acknowledge(self, reminder: Reminder, *, at: datetime | None = None) -> bool:
        """Complete the reserved occurrence after Telegram accepts the message."""
        if not reminder._lease_token:
            return False
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM reminders
                   WHERE chat_id = ? AND id = ? AND active = 1 AND lease_token = ?""",
                (reminder.chat_id, reminder.id, reminder._lease_token),
            ).fetchone()
            if row is None:
                return False
            frequency = row["frequency"]
            if frequency == "once":
                active = 0
                next_run = row["next_run"]
            else:
                active = 1
                next_run = _next_recurrence(row, frequency, max(_utc(at), reminder.next_run))
                next_run = next_run.timestamp()
            connection.execute(
                """UPDATE reminders SET active = ?, next_run = ?, lease_token = NULL,
                   lease_until = 0 WHERE id = ?""",
                (active, next_run, reminder.id),
            )
            return True

    def retry(self, reminder: Reminder, *, at: datetime | None = None) -> bool:
        """Release a failed delivery and let a later poll try it again."""
        if not reminder._lease_token:
            return False
        retry_after = _utc(at).timestamp() + 30
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE reminders SET lease_token = NULL, lease_until = ?
                   WHERE chat_id = ? AND id = ? AND active = 1 AND lease_token = ?""",
                (retry_after, reminder.chat_id, reminder.id, reminder._lease_token),
            )
            return cursor.rowcount == 1
