import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from assistant_bot.bot import Assistant
from assistant_bot.knowledge import Catalog, Evidence, SearchResult, DEMO_CATALOG
from assistant_bot.reminders import ReminderStore
from assistant_bot.state import ConversationStore


class Outbox:
    def __init__(self):
        self.messages = []

    def send(self, chat, text, rows):
        self.messages.append((chat, text, rows or []))

    def button(self, label):
        return next(action["callback_data"] for row in self.messages[-1][2]
                    for action in row if action["text"] == label)


class AssistantTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.db"
        self.time = datetime(2026, 11, 14, 12, tzinfo=UTC)
        self.emitted = Outbox()
        self.catalog = Catalog(DEMO_CATALOG)
        self.start()

    def start(self):
        self.conversations = ConversationStore(self.path)
        self.reminders = ReminderStore(self.path)
        self.bot = Assistant(self.emitted, self.catalog, self.conversations, self.reminders,
                             now=lambda: self.time)

    def click(self, label, chat=1):
        self.bot.callback(chat, self.emitted.button(label))

    def test_plain_followup_keeps_topic_and_new_question_replaces_it(self):
        queries = []
        original = self.catalog.search
        self.catalog.search = lambda query, context: (queries.append(query) or original(query, context))
        self.bot.message(1, "Как оценить эффективность рекламы кофейни?")
        self.bot.message(1, "А если нет данных по кампании?")
        self.assertIn("эффективность рекламы", queries[-1])
        self.assertIn("Уточнение:", queries[-1])
        self.bot.message(1, "Как проверить спрос кофейни?")
        self.assertNotIn("эффективность рекламы", queries[-1])

    def test_negated_launch_statement_does_not_override_operating_business(self):
        self.bot.message(1, "Я не планирую открыть кофейню, она уже работает. Как оценить эффективность рекламы?")
        self.assertEqual(self.conversations.get(1).profile["stage"], "operate")

    def test_personalized_action_survives_followup_and_closes_its_own_schedule(self):
        self.bot.callback(1, "stage:launch")
        self.bot.callback(2, "stage:operate")
        question = "Как оценить эффективность рекламы кофейни?"
        self.bot.message(1, question)
        self.assertIn("До запуска", self.emitted.messages[-1][1])
        self.click("Начать задачу")
        first = self.conversations.get(1).action["id"]
        self.bot.message(2, question)
        self.assertIn("действующую кампанию", self.emitted.messages[-1][1])
        self.bot.callback(1, "action:show")
        self.click("Напомнить")
        self.click("Через минуту")
        self.click("Ежедневно")
        self.click("Сохранить")
        self.bot.message(1, question)
        self.assertEqual(self.conversations.get(1).action["id"], first)
        self.click("Начать задачу")
        self.assertIn("Заменить его новым", self.emitted.messages[-1][1])
        self.start()
        self.time += timedelta(minutes=1)
        self.bot.deliver_reminders()
        self.assertIn("Напоминание", self.emitted.messages[-1][1])
        self.click("Не получается")
        self.assertEqual(self.conversations.get(1).action["status"], "blocked")
        self.bot.message(1, "У меня нет данных по кампании.")
        self.assertEqual(self.conversations.get(1).action["id"], first)
        self.assertIn("доступный показатель", self.emitted.messages[-1][1])
        self.bot.callback(1, f"action:done:{first}")
        self.assertEqual(self.conversations.get(1).action["status"], "done")
        self.assertFalse(self.reminders.list_active(1))

    def test_clarification_and_missing_evidence(self):
        self.bot.message(1, "маркетинг")
        self.assertIn("этап бизнеса", self.emitted.messages[-1][1])
        self.bot.message(1, "Как оценить эффективность рекламы кофейни?")
        self.assertIn("Что нашёл", self.emitted.messages[-1][1])
        self.bot.message(1, "Сколько лун у Юпитера?")
        self.assertIn("не нашёл подтверждённого ответа", self.emitted.messages[-1][1])
        self.assertFalse(self.conversations.get(1).pending)

    def test_business_reminder_can_be_edited_delivered_and_disabled(self):
        self.bot.message(1, "/remind")
        self.click("Работа")
        self.bot.message(1, "Отправить отчёт")
        self.click("Через минуту")
        self.click("Один раз")
        self.click("Сохранить")
        reminder, = self.reminders.list_active(1)
        self.bot.message(1, "/reminders")
        self.click(f"Повтор №{reminder.id}")
        self.click("Еженедельно")
        self.time += timedelta(minutes=1)
        self.bot.deliver_reminders()
        self.assertIn("Отправить отчёт", self.emitted.messages[-1][1])
        self.assertEqual(self.reminders.get(1, reminder.id).next_run,
                         reminder.next_run + timedelta(days=7))
        self.bot.message(1, "/reminders")
        self.click(f"Отключить №{reminder.id}")
        self.time += timedelta(days=7)
        count = len(self.emitted.messages)
        self.bot.deliver_reminders()
        self.assertEqual(len(self.emitted.messages), count)

    def test_reminder_button_reuses_exact_catalog_source(self):
        source = "https://kurs.alfabank.ru/articles/example/"
        evidence = Evidence("<Авторский заголовок>", source,
                            "Сравните результаты кампании за два сопоставимых периода.")
        self.catalog = SimpleNamespace(is_demo=False, search=lambda *_: SearchResult((evidence,), ()))
        self.start()
        self.bot.message(1, "Как сравнить кампании?")
        self.assertIn("&lt;Авторский заголовок&gt;", self.emitted.messages[-1][1])
        self.click("Начать задачу")
        self.click("Напомнить")
        self.click("Через минуту")
        self.click("Один раз")
        self.click("Сохранить")
        self.time += timedelta(minutes=1)
        self.bot.deliver_reminders()
        self.assertEqual(self.emitted.messages[-1][2][0][0]["url"], source)
        self.assertFalse(self.reminders.list_active(1))
