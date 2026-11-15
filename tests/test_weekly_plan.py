import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from assistant_bot.bot import Assistant
from assistant_bot.knowledge import Catalog, Evidence, Material, SearchResult
from assistant_bot.llm import ResponseGenerator
from assistant_bot.reminders import ReminderStore
from assistant_bot.state import ConversationStore
from assistant_bot.weekly_plan import build_plan, next_material


QUESTION = "Кофейня уже работает. Как оценить эффективность рекламы?"
ALTERNATE = "Как проверить спрос до открытия второй точки?"
SOURCE = Evidence(
    "Как оценить эффективность рекламы кофейни",
    "https://kurs.alfabank.ru/articles/eval-ad-measurement/",
    "Выберите один показатель эффективности рекламы и запишите цель теста. "
    "Соберите расходы и число заявок за один и тот же период. "
    "Сравните число заявок для двух сопоставимых рекламных кампаний. "
    "Запишите выбранный показатель, период сравнения и источник информации.",
    kind="article",
    material_id="synthetic-ad-measurement",
)
FOLLOW_UP = Material(
    "Шаблон учёта заявок и расходов на рекламу",
    "https://kurs.alfabank.ru/documents/eval-ad-checklist/",
    "document", "Бесплатно", "Таблица с графами для заявки, периода и расходов.",
    "Поможет сверить заявки с расходами.",
)
PAID_COURSE = Material(
    "Системное обучение настройке рекламы кофейни",
    "https://kurs.alfabank.ru/courses/eval-ad-course/",
    "course", "1 000 ₽", "Учебная карточка системного курса по рекламе.",
    "Для углублённой работы.", 1000, "paid",
)
SECOND_SOURCE = Evidence(
    "Как проверить спрос перед открытием второй точки",
    "https://kurs.alfabank.ru/articles/eval-demand/",
    "Выберите место для проверки спроса по потоку посетителей. "
    "Посчитайте число гостей в два одинаковых периода. "
    "Соберите отклики на тестовое предложение в выбранном месте. "
    "Сравните отклики с планом и запишите результат проверки спроса.",
    kind="article",
    material_id="synthetic-demand",
)
GLOSSARY = Evidence(
    "NPS — индекс лояльности",
    "https://kurs.alfabank.ru/glossary/eval-nps/",
    "NPS — индекс лояльности, основанный на ответах клиентов.",
    kind="glossary",
    material_id="synthetic-nps",
)
CLOSED_TEXT = "Раскройте синтетическую закрытую инструкцию из платного урока кофейни."


class Catalogue:
    # Exercise validation of case-format links without opening any URL.
    is_demo = False

    def __init__(self, recommendations=(FOLLOW_UP, PAID_COURSE)):
        self.recommendations = recommendations
        self.extra_evidence = ()
        self.hold_ad_sources = False

    def search(self, question, context=None):
        lower = question.casefold()
        if lower.strip(" ?") == "nps":
            return SearchResult((GLOSSARY,))
        if lower.strip(" ?") == "маркетинг":
            return SearchResult(needs_clarification=True)
        if "фотон" in lower:
            return SearchResult()
        if "спрос" in lower or "второй точки" in lower:
            return SearchResult((SECOND_SOURCE,))
        if "реклам" in lower or "заяв" in lower:
            if self.hold_ad_sources:
                return SearchResult()
            return SearchResult((SOURCE, *self.extra_evidence), self.recommendations)
        return SearchResult()


class Outbox:
    def __init__(self):
        self.messages = []

    def send(self, chat, text, rows=None):
        self.messages.append((chat, text, rows or []))

    def answer_callback(self, callback_id):
        pass

    def last(self, chat):
        return next((text, rows) for owner, text, rows in reversed(self.messages) if owner == chat)


class WeeklyPlanTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "state.db"
        self.outbox = Outbox()
        self.catalogue = Catalogue()
        self.now = datetime(2026, 11, 15, 12, tzinfo=UTC)
        self.restart()

    def restart(self, generator=None):
        self.store = ConversationStore(self.db)
        self.reminders = ReminderStore(self.db)
        self.bot = Assistant(self.outbox, self.catalogue, self.store, self.reminders,
                             now=lambda: self.now, generator=generator)

    def message(self, text, chat=1):
        self.bot.update({"message": {"chat": {"type": "private", "id": chat}, "text": text}})

    def callback(self, data, chat=1):
        self.bot.update({"callback_query": {"id": "synthetic",
                                            "message": {"chat": {"type": "private", "id": chat}},
                                            "data": data}})

    def buttons(self, chat=1):
        return {item["text"]: item for row in self.outbox.last(chat)[1] for item in row}

    def text(self, chat=1):
        return self.outbox.last(chat)[0]

    def click(self, label, chat=1):
        options = self.buttons(chat)
        self.assertIn(label, options)
        data = options[label]["callback_data"]
        self.assertLessEqual(len(data.encode()), 64)
        self.callback(data, chat)
        return data

    def click_prefix(self, prefix, chat=1):
        matches = [label for label in self.buttons(chat) if label.startswith(prefix)]
        self.assertEqual(len(matches), 1, self.buttons(chat))
        return self.click(matches[0], chat)

    def bootstrap(self, chat=1, language="Русский"):
        self.message("/start", chat)
        self.click(language, chat)

    def offer(self, chat=1, question=QUESTION):
        self.message(question, chat)
        self.click("Составить план на неделю", chat)
        if "Начать план" not in self.buttons(chat):
            self.message("30 минут в день", chat)
        self.assertIn("Начать план", self.buttons(chat))
        self.assertIn("Отказаться", self.buttons(chat))

    def start_action(self, chat=1):
        callback = self.click("Начать действие", chat)
        task = self.store.get(chat).action
        self.assertEqual(task["status"], "active")
        self.assertGreater(len(task["title"]), 0)
        self.assertIn("Сделано", self.buttons(chat))
        return callback, task["id"]

    def prepare_reminder(self, chat=1):
        self.click("Напомнить", chat)
        self.click("Через минуту", chat)
        self.click("Ежедневно", chat)

    def remind(self, chat=1):
        self.prepare_reminder(chat)
        save = self.click("Сохранить", chat)
        reminder, = self.reminders.list_active(chat)
        return save, reminder

    def report_result(self, result, note=None, chat=1):
        self.click("Сделано", chat)
        return self.report_selected_outcome(result, note, chat)

    def report_selected_outcome(self, result, note=None, chat=1):
        self.assertTrue({"Получилось", "Получилось частично", "Не получилось"} <= self.buttons(chat).keys())
        callback = self.click(result, chat)
        if note is None:
            self.click("Пропустить", chat)
        else:
            self.message(note, chat)
        return callback

    def plan(self, chat=1):
        return self.store.get(chat).weekly_plan

    def failing_generator(self):
        generator = ResponseGenerator(
            "xai", "synthetic-token", "synthetic-model", allow_case_data=True,
            test_chat_ids=(1,), test_expires_at=datetime.now(UTC) + timedelta(days=1),
            max_requests=1, stream=False, budget_path=self.db.with_name("budget.db"),
        )
        transport = Mock(side_effect=OSError("synthetic provider outage"))
        generator.opener = Mock(open=transport)
        return generator, transport

    def test_non_actionable_answer_never_offers_plan(self):
        self.bootstrap()
        for question in ("NPS", "Сколько фотонов у света?", "Маркетинг"):
            with self.subTest(question=question):
                self.message(question)
                self.assertNotIn("Составить план на неделю", self.buttons())
                self.message("/cancel")
        self.assertFalse(self.reminders.list_active(1))

    def test_english_menu_opens_weekly_plan(self):
        self.bootstrap(language="English")
        plan_button, = [
            label for label in self.buttons() if "plan" in label.casefold()
        ]
        self.assertRegex(plan_button.casefold(), r"week")
        self.click(plan_button)
        self.assertRegex(self.text().casefold(), r"week|plan")

    def test_offer_is_explicit_and_reject_does_not_create_task_or_reminders(self):
        self.bootstrap()
        self.offer()
        self.assertFalse(self.store.get(1).action)
        self.assertFalse(self.reminders.list_active(1))
        accept = self.buttons()["Начать план"]["callback_data"]
        self.click("Отказаться")
        self.callback(accept)
        self.message("/plan")
        self.assertNotIn("Начать действие", self.buttons())
        self.assertFalse(self.store.get(1).action)
        self.assertFalse(self.reminders.list_active(1))

    def test_progress_and_linked_notification_survive_restart(self):
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        self.assertFalse(self.reminders.list_active(1))
        action_button, action_id = self.start_action()
        self.callback(action_button)
        self.assertEqual(self.store.get(1).action["id"], action_id)
        self.message("/step")
        save, reminder = self.remind()
        self.callback(save)
        self.assertEqual(self.reminders.list_active(1), [reminder])
        self.assertEqual(reminder.action_url, self.store.get(1).action["source"])
        self.restart()
        self.message("/plan")
        self.assertEqual(self.store.get(1).action["id"], action_id)
        self.now += timedelta(minutes=1)
        self.bot.deliver_reminders()
        self.assertIn("Сделано", self.buttons())
        self.assertIn(reminder.action_url, {item.get("url") for item in self.buttons().values()})
        self.click("Сделано")
        self.assertFalse(self.reminders.get(1, reminder.id).active)

    def test_stale_confirmation_cannot_confirm_a_new_reminder_draft(self):
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        self.start_action()
        self.prepare_reminder()
        old = self.buttons()["Сохранить"]["callback_data"]
        self.click("Отмена")
        self.message("/step")
        self.prepare_reminder()
        current = self.buttons()["Сохранить"]["callback_data"]
        self.assertNotEqual(old, current)
        draft = self.store.get(1).pending
        self.callback(old)
        self.assertFalse(self.reminders.list_active(1))
        self.assertEqual(self.store.get(1).pending, draft)
        self.callback(current)
        self.assertEqual(len(self.reminders.list_active(1)), 1)

    def test_partial_outcome_rework_then_success_preserves_results_and_continues(self):
        self.bootstrap()
        self.message(QUESTION)
        self.click("Составить план на неделю")
        self.assertNotIn("Начать план", self.buttons(), "should ask for time when it is unknown")
        self.message("30 минут в день")
        self.click("Начать план")
        plan_id = self.plan()["id"]
        steps = self.plan()["steps"]
        self.assertEqual(self.plan()["days"], 7)
        self.assertGreaterEqual(len(steps), 3)
        self.assertLessEqual(len(steps), 5)
        self.assertEqual([step["day"] for step in steps], sorted(step["day"] for step in steps))
        for step in steps:
            self.assertTrue(all(step[key] for key in ("title", "why", "instruction", "completion")))
            self.assertIn(step["day"], range(1, 8))
            self.assertIn(step["minutes"], range(1, 31))
            self.assertEqual(step["source"]["url"], SOURCE.url)
        self.start_action()
        task = self.store.get(1).action
        self.assertEqual(task["plan_id"], plan_id)
        self.assertIn("plan_step", task)
        self.remind()
        self.message("/step")
        stale_result = self.report_result(
            "Получилось частично", "Получилось собрать 8 заявок, но нет данных по расходам."
        )
        partial_step, *remaining = self.plan()["steps"]
        self.assertEqual(partial_step["status"], "partial")
        result, = partial_step["results"]
        self.assertEqual(result["execution"], "completed")
        self.assertEqual(result["outcome"], "partial")
        self.assertEqual(result["metrics"], {"leads": "8"})
        self.assertTrue(all(step["status"] == "todo" for step in remaining))
        self.assertFalse(self.reminders.list_active(1))
        self.assertIn("Доработать действие", self.buttons())
        self.assertTrue(any(item.get("url") == FOLLOW_UP.url for item in self.buttons().values()))
        self.assertIn(result["material"]["reason"], self.text())
        self.restart()
        self.message("/plan")
        self.assertEqual(self.plan()["steps"][0], partial_step)
        self.click("Открыть действие")
        self.click("Доработать действие")
        self.click("Сделано")
        selected = self.buttons()["Получилось"]["callback_data"]
        current_review = self.plan()
        self.callback(stale_result)
        self.assertEqual(self.plan(), current_review)
        self.callback(selected)
        self.click("Пропустить")
        self.assertEqual(self.plan()["steps"][0]["status"], "done")
        self.assertGreaterEqual(len(self.plan()["steps"][0]["results"]), 2)
        self.assertEqual(self.plan()["steps"][1]["status"], "todo")
        self.click("Следующее действие")
        self.assertEqual(self.plan()["id"], plan_id)
        self.assertTrue({"Начать действие", "Сделано"} & self.buttons().keys())

    def test_failed_outcome_requires_explicit_retry_and_supports_plan_controls(self):
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        self.start_action()
        self.click("Отложить")
        self.assertEqual(self.plan()["steps"][0]["status"], "deferred")
        self.click("Продолжить действие")
        self.report_result("Не получилось", "Нет доступа к рекламному кабинету.")
        self.assertEqual(self.plan()["steps"][0]["status"], "failed")
        self.assertNotIn("Следующее действие", self.buttons())
        self.assertIn("Доработать действие", self.buttons())
        self.message("/plan")
        self.click("Приостановить план")
        self.assertEqual(self.plan()["status"], "paused")
        self.restart()
        self.message("/plan")
        self.click("Продолжить план")
        self.assertEqual(self.plan()["status"], "active")
        self.click("Отменить план")
        if self.plan()["status"] != "cancelled":
            self.click_prefix("Подтверд")
        self.assertEqual(self.plan()["status"], "cancelled")
        self.assertFalse(self.reminders.list_active(1))

    def test_blocker_records_an_attempt_and_shows_an_alternative(self):
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        self.start_action()
        self.click("Не получается")
        self.message("У меня нет данных по расходам.")
        blocked = self.plan()["steps"][0]
        self.assertEqual(blocked["status"], "failed")
        result, = blocked["results"]
        self.assertEqual(result["execution"], "attempted")
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(self.plan()["steps"][1]["status"], "todo")
        self.assertIn("Доработать действие", self.buttons())
        self.assertIn("один доступный показатель", self.text())

    def test_plan_finishes_only_after_the_last_action_is_verified(self):
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        total = len(self.plan()["steps"])
        for i in range(total):
            self.start_action()
            self.click("Сделано")
            self.assertEqual(self.plan()["status"], "active")
            self.report_selected_outcome("Получилось")
            if i < total - 1:
                self.assertEqual(self.plan()["status"], "active")
                self.click("Следующее действие")
            else:
                self.assertEqual(self.plan()["status"], "done")
                self.assertNotIn("Следующее действие", self.buttons())
        self.assertTrue(all(step["status"] == "done" for step in self.plan()["steps"]))

    def test_active_task_and_plan_are_replaced_only_after_confirmation(self):
        self.bootstrap()
        self.message(QUESTION)
        self.click("Начать задачу")
        old_task = self.store.get(1).action["id"]
        _, old_reminder = self.remind()
        self.offer()
        self.click("Начать план")
        self.assertEqual(self.store.get(1).action["id"], old_task)
        self.assertTrue(self.reminders.get(1, old_reminder.id).active)
        self.click("Заменить и начать")
        self.assertFalse(self.reminders.get(1, old_reminder.id).active)
        original_plan = self.plan()["id"]
        stale_action, new_task = self.start_action()

        self.offer(question=ALTERNATE)
        self.click("Начать план")
        self.assertEqual(self.plan()["id"], original_plan)
        self.assertEqual(self.store.get(1).action["id"], new_task)
        self.click("Заменить и начать")
        self.assertNotEqual(self.plan()["id"], original_plan)
        snapshot = self.plan()
        self.callback(stale_action)
        self.assertEqual(self.plan(), snapshot)
        self.assertFalse(self.reminders.list_active(1))

    def test_replacement_confirmation_cannot_replace_a_newer_task(self):
        self.bootstrap()
        self.message(QUESTION)
        self.click("Начать задачу")
        old = self.store.get(1).action["id"]
        self.offer()
        self.click("Начать план")
        stale = self.buttons()["Заменить и начать"]["callback_data"]
        self.message(ALTERNATE)
        self.click("Начать задачу")
        self.click("Заменить задачу")
        new = self.store.get(1).action
        self.assertNotEqual(new["id"], old)
        self.callback(stale)
        self.assertEqual(self.store.get(1).action, new)
        self.assertFalse(self.plan())

    def test_result_note_prompt_reopens_after_restart(self):
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        self.start_action()
        self.click("Сделано")
        self.click("Получилось частично")
        pending = self.store.get(1).pending
        self.assertEqual(pending["kind"], "weekly_note")
        self.restart()
        self.message("/plan")
        self.assertEqual(self.store.get(1).pending, pending)
        if "Пропустить" not in self.buttons():
            self.click("Открыть действие")
        self.assertIn("Пропустить", self.buttons())
        self.message("Получил 5 заявок.")
        result, = self.plan()["steps"][0]["results"]
        self.assertEqual(result["metrics"], {"leads": "5"})
        self.assertFalse(self.store.get(1).pending)

    def test_followup_without_new_material_does_not_invent_one(self):
        self.catalogue.recommendations = ()
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        self.start_action()
        self.catalogue.hold_ad_sources = True
        self.report_result("Получилось частично", "Результат есть, но осталось проверить период.")
        self.assertIn("Доработать действие", self.buttons())
        self.assertRegex(self.text().casefold(), r"не наш[её]л|не найден|нового материала пока нет")
        self.assertTrue({item["url"] for item in self.buttons().values() if "url" in item}
                        <= {SOURCE.url})

    def test_paid_followup_requires_a_learning_request_and_shows_only_card(self):
        self.catalogue.extra_evidence = (
            Evidence("Учебный закрытый урок по рекламе", PAID_COURSE.url, CLOSED_TEXT,
                     kind="lesson", content_scope="closed", material_id="synthetic-closed"),
        )
        self.bootstrap()
        self.offer()
        self.assertNotIn(CLOSED_TEXT, self.text())
        self.click("Начать план")
        self.start_action()
        self.report_result("Получилось частично", "Хочу пройти подробный курс по рекламе.")
        self.assertIn(PAID_COURSE.url, {item.get("url") for item in self.buttons().values()})
        self.assertRegex(self.text(), r"системн.{0,5} обучени|подробн.{0,5} курс")
        self.assertIn(PAID_COURSE.price_display, self.text())
        self.assertIn(PAID_COURSE.description, self.text())
        self.assertNotIn(CLOSED_TEXT, self.text())
        self.assertNotIn(CLOSED_TEXT, json.dumps(self.plan(), ensure_ascii=False))

    def test_clicks_from_another_chat_cannot_activate_someone_elses_plan(self):
        self.bootstrap()
        self.offer()
        accept = self.buttons()["Начать план"]["callback_data"]
        self.bootstrap(chat=2)
        self.callback(accept, chat=2)
        self.assertFalse(self.plan(2))
        self.assertFalse(self.store.get(2).action)
        self.assertFalse(self.plan())
        self.click("Начать план")
        begin = self.buttons()["Начать действие"]["callback_data"]
        self.callback(begin, chat=2)
        self.assertFalse(self.store.get(2).action)
        self.assertFalse(self.store.get(1).action)
        self.click("Начать действие")
        self.assertTrue(self.store.get(1).action)

    def test_model_failure_does_not_mutate_plan_or_spend_attempts_on_clicks(self):
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        self.start_action()
        expected = self.plan()
        generator, transport = self.failing_generator()
        self.restart(generator)
        self.message(ALTERNATE)
        transport.assert_called_once()
        self.assertEqual(generator.last_status, "unavailable")
        self.assertEqual(self.plan(), expected)
        self.message(QUESTION)
        self.assertEqual(self.plan(), expected)
        self.assertEqual(generator.last_status, "budget")
        self.assertEqual(generator.remaining_requests, 0)
        self.message("/plan")
        self.click("Приостановить план")
        self.click("Продолжить план")
        transport.assert_called_once()
        self.assertEqual(self.plan()["id"], expected["id"])

    def test_model_failure_can_offer_a_grounded_plan_without_an_extra_attempt(self):
        generator, transport = self.failing_generator()
        generator.generate = Mock(wraps=generator.generate)
        self.restart(generator)
        self.bootstrap()
        self.message(QUESTION)
        self.assertEqual(generator.last_status, "unavailable")
        self.assertNotIn("Начать задачу", self.buttons())
        self.assertIn("Не удалось подготовить ответ", self.text())
        self.click("Составить план на неделю")
        self.message("30 минут в день")
        self.click("Начать план")
        self.start_action()
        self.click("Не получается")
        self.click("Пропустить")
        self.assertEqual(self.plan()["steps"][0]["status"], "failed")
        self.assertTrue(all(step["source"]["url"] == SOURCE.url for step in self.plan()["steps"]))
        transport.assert_called_once()
        generator.generate.assert_called_once()

    def test_sensitive_result_note_never_persists_raw_contact_details(self):
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        self.start_action()
        self.click("Сделано")
        self.click("Получилось частично")
        email = "poc-owner@example.test"
        self.message(f"Есть 8 заявок. Моя личная почта {email}.")
        saved = json.dumps(vars(self.store.get(1)), ensure_ascii=False)
        self.assertNotIn(email, saved)
        self.assertNotIn(email, self.text())
        self.assertEqual(self.plan()["steps"][1]["status"], "todo")

    def test_old_state_reopens_without_migration(self):
        older = {
            "profile": {"stage": "operate", "sector": "Кофейня"},
            "pending": {}, "action": {}, "proposal": {},
            "topic": "Старая задача", "language": "ru",
        }
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO conversations VALUES (?, ?)", (1, json.dumps(older)))
        self.restart()
        self.message("/plan")
        self.assertEqual(self.store.get(1).topic, older["topic"])
        self.assertEqual(self.store.get(1).profile, older["profile"])
        self.assertEqual(self.store.get(1).language, "ru")
        self.assertNotIn("Начать действие", self.buttons())

    def test_existing_plan_without_new_keys_retains_results(self):
        self.bootstrap()
        self.offer()
        self.click("Начать план")
        self.start_action()
        self.report_result("Получилось частично", "Нет данных о расходах.")
        state = self.store.get(1)
        old_plan = state.weekly_plan
        old_plan.pop("used")
        for step in old_plan["steps"]:
            for key in ("kind", "subject", "artifact", "requires", "search_terms", "reduced"):
                step.pop(key, None)
            step["source"].pop("heading", None)
            step["source"].pop("excerpt", None)
        self.store.save(1, state)
        self.restart()
        self.message("/step")
        self.click("Доработать действие")
        self.report_result("Получилось", "Есть исходный период и число заявок.")
        first = self.plan()["steps"][0]
        self.assertEqual([result["outcome"] for result in first["results"]], ["partial", "success"])
        self.assertIn("Следующее действие", self.buttons())


class WeeklyPlanningContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = Catalog(Path(__file__).resolve().parents[1] / "assistant_bot" / "demo_catalog.jsonl")

    def fixture(self, title, passage, *, scope="public_text"):
        evidence = Evidence(title, "https://kurs.alfabank.ru/articles/plan-example/",
                            passage, content_scope=scope)
        return Mock(is_demo=False, search=Mock(return_value=SearchResult((evidence,))), public_fragments=None)

    def test_demo_ad_plan_has_distinct_artifacts_and_ordered_prerequisites(self):
        plan = build_plan(self.demo, "Как оценить эффективность рекламы кофейни?", {}, minutes=30)
        self.assertGreaterEqual(len(plan["steps"]), 3)
        artifacts = set()
        for step in plan["steps"]:
            self.assertTrue(set(step["requires"]) <= artifacts)
            self.assertNotIn(step["artifact"], artifacts)
            self.assertTrue(step["source"]["excerpt"])
            self.assertTrue(step["source"]["heading"])
            artifacts.add(step["artifact"])
        self.assertIn("cost_per_order_value", artifacts)
        self.assertNotIn("Проверьте результат", [step["title"] for step in plan["steps"]])

    def test_demo_ad_plan_without_past_statistics_sets_up_future_measurement(self):
        plan = build_plan(self.demo, "Как оценить эффективность рекламы кофейни? Нет прошлой статистики.", {},
                          minutes=30)
        self.assertTrue(plan["steps"])
        self.assertFalse({"calculate", "compare"} & {s["kind"] for s in plan["steps"]})
        self.assertTrue(any("форма будущего учёта" in s["completion"] for s in plan["steps"]))

    def test_advertising_does_not_replace_requested_leads_with_order_cost(self):
        plan = build_plan(self.demo, "Как оценить эффективность рекламы кофейни? Хочу считать заявки.", {},
                          minutes=30)
        self.assertTrue(plan["steps"])
        self.assertNotIn("cost_per_order", {s["subject"] for s in plan["steps"]})
        self.assertIn("число заявок", plan["steps"][0]["instruction"])

    def test_romi_keeps_distinct_inputs_when_leads_were_not_tracked(self):
        catalog = self.fixture(
            "Как оценить окупаемость рекламы",
            "Рассчитайте маржинальную прибыль от заказов. Для этого из выручки вычтите себестоимость товара. "
            "ROMI = (маржинальная прибыль − расходы на рекламу) / расходы на рекламу × 100%.")
        plan = build_plan(catalog, "Как оценить окупаемость рекламы? Не записывали заявки.", {}, minutes=30)
        self.assertEqual([s["artifact"] for s in plan["steps"]],
                         ["attributed_finances", "campaign_margin_value", "romi_value"])
        self.assertIn("связанные продажи", plan["steps"][0]["instruction"])
        self.assertIn("Если известны", plan["steps"][-1]["instruction"])
        self.assertNotIn("заявок", " ".join(s["completion"] for s in plan["steps"]))

    def test_break_even_keeps_units_and_does_not_calculate_from_a_draft(self):
        catalog = self.fixture(
            "Маржинальность и точка безубыточности",
            "Постоянные затраты не зависят от выручки. Маржа = выручка − переменные затраты. "
            "Точка безубыточности = постоянные затраты / маржинальность.")
        full = build_plan(catalog, "Как рассчитать точку безубыточности?", {}, minutes=45)
        short = build_plan(catalog, "Как рассчитать точку безубыточности за 20 минут в день?", {}, minutes=45)
        self.assertEqual([s["kind"] for s in full["steps"]], ["collect", "calculate", "calculate"])
        self.assertIn("0,4 для 40%", full["steps"][-1]["instruction"])
        self.assertEqual(len(short["steps"]), 1)
        self.assertTrue(short["steps"][0]["reduced"])
        self.assertTrue(short["incomplete"])

    def test_opening_plan_with_20_minutes_limits_scope(self):
        catalog = self.fixture(
            "Как составить бизнес-план",
            "Опишите продукт или услугу. Исследуйте рынок. Составьте финансовый план.")
        plan = build_plan(catalog, "Как составить бизнес-план за 20 минут в день?", {}, minutes=60)
        self.assertEqual({s["artifact"] for s in plan["steps"]},
                         {"offer_draft", "competitor_snapshot", "startup_inputs_outline"})
        self.assertTrue(all(s["minutes"] <= 20 for s in plan["steps"]))
        self.assertTrue(plan["incomplete"])
        self.assertIn("финансовый прогноз потребуют отдельных проверок", plan["limitation"])

    def test_ambiguous_or_limited_demand_gets_only_sourced_work(self):
        plan = build_plan(self.demo, "Как проверить спрос до открытия кофейни?", {"stage": "launch"}, minutes=20)
        self.assertEqual([s["artifact"] for s in plan["steps"]], ["foot_traffic_observations"])
        self.assertTrue(plan["incomplete"])
        unsupported = self.fixture("Исследования спроса", "Проведите интервью с покупателями.",
                                   scope="document_description")
        plan = build_plan(unsupported, "Как проверить спрос на товар?", {}, minutes=60)
        self.assertFalse(plan["steps"])

    def test_employee_sales_kpis_do_not_become_a_general_retention_plan(self):
        source = self.fixture("Показатели эффективности менеджеров по продажам",
                              "Регулярный сбор обратной связи. Обсудите с сотрудником его цели.")
        self.assertFalse(build_plan(source, "Как удержать сотрудников магазина?", {}, minutes=60)["steps"])

    def test_followup_matches_action_and_does_not_repeat_material(self):
        action = {"title": "Уточните учёт", "source": {"url": SOURCE.url}, "search_terms": r"расход|заяв"}
        catalog = Catalogue()
        selected = next_material(catalog, QUESTION, {}, step=action)
        self.assertEqual(selected["url"], FOLLOW_UP.url)
        self.assertIn(action["title"], selected["reason"])
        self.assertIsNone(next_material(catalog, QUESTION, {}, step=action, used=[FOLLOW_UP.url]))
        irrelevant = {"title": "Проверьте границы кампании", "source": {"url": SOURCE.url},
                      "search_terms": r"сопоставим\w+ период"}
        self.assertIsNone(next_material(catalog, QUESTION, {}, step=irrelevant))


if __name__ == "__main__":
    unittest.main()
