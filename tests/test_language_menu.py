import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from assistant_bot.bot import Assistant
from assistant_bot.knowledge import Evidence, SearchResult
from assistant_bot.reminders import ReminderStore
from assistant_bot.state import ConversationStore


class Outbox:
    def __init__(self):
        self.messages = []
        self.acknowledged = []

    def send(self, chat, text, rows):
        self.messages.append((chat, text, rows or []))

    def answer_callback(self, callback_id):
        self.acknowledged.append(callback_id)

    def buttons(self):
        return {
            button["callback_data"]: button["text"]
            for row in self.messages[-1][2]
            for button in row
            if "callback_data" in button
        }


class LanguageMenuTest(unittest.TestCase):
    def test_first_reminder_request_starts_reminder_setup_after_language_choice(self):
        self.bot.update({
            "message": {"chat": {"id": 1, "type": "private"}, "text": "Remind me to send the report"}
        })
        self.select("en")
        self.assertEqual(self.conversations.get(1).pending["title"], "send the report")
        self.assertIn("rem:type:business", self.outbox.buttons())
        self.search.assert_not_called()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "state.db"
        self.now = datetime(2026, 11, 14, 12, tzinfo=UTC)
        self.outbox = Outbox()
        self.evidence = Evidence(
            "Как оценить эффективность рекламы",
            "https://kurs.alfabank.ru/articles/example/",
            "Сравните количество заказов за два сопоставимых периода.",
        )
        self.search = Mock(return_value=SearchResult((self.evidence,), ()))
        self.catalog = SimpleNamespace(is_demo=False, search=self.search)
        self.runner()

    def runner(self):
        self.conversations = ConversationStore(self.path)
        self.reminders = ReminderStore(self.path)
        self.bot = Assistant(self.outbox, self.catalog, self.conversations, self.reminders, now=lambda: self.now)

    def select(self, language, chat=1):
        self.bot.callback(chat, f"lang:{language}")

    def test_start_selection_survives_restart_and_is_per_chat(self):
        self.assertEqual(self.conversations.get(1).language, "")
        self.bot.message(1, "/start")
        self.assertEqual(self.outbox.buttons()["lang:ru"], "Русский")
        self.assertEqual(self.outbox.buttons()["lang:en"], "English")
        self.select("en")

        english_home = self.outbox.buttons()
        core_callbacks = {"menu:ask", "action:show", "reminders", "menu:help", "menu:about"}
        self.assertTrue(core_callbacks <= english_home.keys())
        self.assertRegex(english_home["menu:ask"], r"(?i)\bask\b")

        self.runner()
        self.bot.message(1, "/start")
        self.assertEqual(self.conversations.get(1).language, "en")
        self.assertEqual(self.outbox.buttons(), english_home)
        self.bot.message(2, "/start")
        self.assertIn("lang:ru", self.outbox.buttons())
        self.select("ru", chat=2)
        self.assertEqual(self.conversations.get(2).language, "ru")
        self.assertEqual(self.conversations.get(1).language, "en")
        self.assertTrue(core_callbacks <= self.outbox.buttons().keys())
        self.assertRegex(self.outbox.buttons()["menu:ask"], r"(?i)вопрос")
        self.search.assert_not_called()

    def test_help_about_and_language_switch_keep_active_state(self):
        self.bot.message(1, "/start")
        self.select("ru")
        state = self.conversations.get(1)
        state.profile["timezone"] = "Asia/Yekaterinburg"
        reminder = self.reminders.create(
            1, "Сравнить заказы", self.now + timedelta(days=1), "Asia/Yekaterinburg", "weekly", now=self.now
        )
        state.action = {"id": "saved-action", "status": "active", "reminder_ids": [reminder.id]}
        state.pending = {"kind": "reminder", "step": "when", "title": "Провести проверку"}
        state.topic = "Исходный вопрос о рекламе"
        self.conversations.save(1, state)

        self.bot.message(1, "/help")
        self.assertEqual(self.conversations.get(1).pending, state.pending)
        self.bot.callback(1, "menu:help")
        self.assertEqual(self.conversations.get(1).pending, state.pending)
        self.assertIn("/remind", self.outbox.messages[-1][1])
        self.bot.callback(1, "menu:about")
        self.assertEqual(self.conversations.get(1).pending, state.pending)
        self.assertIn("Альфа-Курса", self.outbox.messages[-1][1])
        self.bot.message(1, "/language")
        self.assertTrue({"lang:ru", "lang:en"} <= self.outbox.buttons().keys())
        self.select("en")

        self.runner()
        saved = self.conversations.get(1)
        self.assertEqual(saved.language, "en")
        self.assertEqual(saved.profile["timezone"], "Asia/Yekaterinburg")
        self.assertEqual(saved.action, state.action)
        self.assertEqual(saved.pending, state.pending)
        self.assertEqual(saved.topic, state.topic)
        self.assertEqual(self.reminders.list_active(1), [reminder])
        self.search.assert_not_called()

    def test_first_telegram_question_is_processed_after_english_choice(self):
        question = "Как оценить эффективность рекламы кофейни?"
        self.bot.update({
            "message": {"chat": {"id": 1, "type": "private"}, "from": {"language_code": "en"}, "text": question}
        })

        self.assertTrue({"lang:ru", "lang:en"} <= self.outbox.buttons().keys())
        self.search.assert_not_called()
        self.bot.update({
            "callback_query": {
                "id": "select-english",
                "message": {"chat": {"id": 1, "type": "private"}},
                "data": "lang:en",
            }
        })

        self.assertEqual(self.outbox.acknowledged, ["select-english"])
        self.assertEqual(self.conversations.get(1).language, "en")
        self.assertEqual(self.search.call_count, 1)
        self.assertEqual(self.search.call_args.args[0], question)

    def test_english_action_buttons_and_reminder_delivery(self):
        self.select("en")
        self.bot.callback(1, "menu:ask")
        self.bot.message(1, "How do I measure ad performance for my coffee shop?")
        self.assertIn("эффективность рекламы", self.search.call_args.args[0])
        self.assertIn("What to do next", self.outbox.messages[-1][1])
        action = self.conversations.get(1).proposal["id"]
        self.bot.callback(1, f"action:take:{action}")
        self.assertEqual(self.outbox.buttons()[f"action:remind:{action}"], "Remind me about this step")
        self.bot.callback(1, f"action:remind:{action}")
        self.bot.callback(1, "rem:in:1")
        self.assertEqual(self.outbox.buttons()["rem:freq:once"], "Once")
        self.bot.callback(1, "rem:freq:once")
        self.bot.callback(1, "rem:confirm")
        self.assertIn("created", self.outbox.messages[-1][1])
        self.now += timedelta(minutes=1)
        self.bot.deliver_reminders()
        self.assertIn("<b>Reminder</b>", self.outbox.messages[-1][1])
        self.assertEqual(self.outbox.buttons()[f"action:done:{action}"], "Done")
