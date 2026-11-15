import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event
from urllib.error import URLError
from unittest.mock import Mock, patch

from assistant_bot.__main__ import make_assistant
from assistant_bot.budget import reserve_request
from assistant_bot.knowledge import Evidence, SearchResult
from assistant_bot.llm import ResponseGenerator, generator_from_env
from assistant_bot.privacy import validate_outbound


QUESTION = "Как оценить эффективность рекламы кофейни?"
QUOTE = "Сравните расходы на рекламу и число заказов за один и тот же период."
SOURCE = Evidence("Эффективность рекламы кофейни", "https://kurs.alfabank.ru/articles/ads/", QUOTE)
ANSWER = {"status": "answered", "answer": QUOTE, "source_index": 0, "source_indices": [0],
          "quote": QUOTE, "action": "Соберите расходы и число заказов за неделю.",
          "completion": "В таблице есть расходы и число заказов за неделю."}


class Catalogue:
    is_demo = False

    def search(self, query, context=None):
        return SearchResult((SOURCE,))


class Outbox:
    def __init__(self):
        self.messages = {}

    def send(self, chat, text, rows=None):
        self.messages[chat] = (text, {button["text"]: button.get("callback_data", "")
                                     for row in rows or [] for button in row})


def transport_response():
    envelope = {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": json.dumps(ANSWER, ensure_ascii=False)},
    ]}]}
    return BytesIO(json.dumps(envelope, ensure_ascii=False).encode())


class PublicModeTests(unittest.TestCase):
    def setUp(self):
        files = tempfile.TemporaryDirectory()
        self.addCleanup(files.cleanup)
        self.state = Path(files.name) / "state.db"
        self.budget = Path(files.name) / "budget.db"
        self.outbox = Outbox()

    def generator(self, **options):
        config = {"LLM_MODE": "public", "LLM_PROVIDER": "xai", "XAI_API_KEY": "synthetic",
                  "LLM_ALLOW_CASE_DATA": "true", "LLM_STREAM": "false", "LLM_BUDGET_PATH": str(self.budget),
                  "LLM_TEST_CHAT_IDS": "99", "LLM_TEST_EXPIRES_AT": "2000-01-01T00:00:00+00:00"}
        config.update(options)
        generator = generator_from_env(config)
        generator.opener = Mock()
        generator.opener.open.side_effect = lambda *args, **kwargs: transport_response()
        return generator

    def bot(self, generator):
        return make_assistant(self.outbox, Catalogue(), self.state, generator=generator)

    def click(self, bot, chat, label):
        callback = self.outbox.messages[chat][1][label]
        self.assertLessEqual(len(callback.encode()), 64)
        bot.callback(chat, callback)
        return callback

    def allow(self, generator, chat):
        access = generator.public_access
        self.assertTrue(access.choose(chat, access.issue_notice(chat), "granted"))

    def generate(self, generator, chat=1, source=SOURCE):
        return generator.generate(QUESTION, {"stage": "operate", "timezone": "Europe/Moscow"}, (source,),
                                  chat_id=chat)

    def test_new_public_user_explicitly_consents_before_official_catalog_reaches_transport(self):
        generator = self.generator()
        bot = self.bot(generator)
        bot.message(1, QUESTION)
        text, buttons = self.outbox.messages[1]
        self.assertIn("xAI", text)
        for element in ("вопрос", "цель бизнеса", "фрагменты", "/privacy", SOURCE.title):
            self.assertIn(element, text)
        self.assertIn("Составить план на неделю", buttons)
        self.assertIn("Дать согласие", buttons)
        generator.opener.open.assert_not_called()
        self.assertEqual(generator.public_access.decision(1), "")
        button = self.click(bot, 1, "Дать согласие")
        generator.opener.open.assert_not_called()
        self.assertEqual(generator.public_access.decision(1), "granted")
        bot.callback(2, button)
        self.assertEqual(generator.public_access.decision(2), "")
        bot.message(1, QUESTION)
        generator.opener.open.assert_called_once()
        self.assertEqual(generator.last_status, "answered")
        document = json.loads(generator.opener.open.call_args.args[0].data)
        self.assertIs(document["store"], False)
        outbound = json.loads(document["input"][1]["content"])
        self.assertEqual(set(outbound), {"question", "context", "obstacle", "passages"})

    def test_declining_revocation_restart_and_stale_buttons(self):
        generator = self.generator(LLM_PUBLIC_USER_LIMIT="1")
        bot = self.bot(generator)
        bot.message(1, QUESTION)
        stale_grant = self.outbox.messages[1][1]["Дать согласие"]
        self.click(bot, 1, "Продолжить без модели")
        bot.callback(1, stale_grant)
        bot.message(1, QUESTION)
        self.assertIn(SOURCE.title, self.outbox.messages[1][0])
        self.assertNotIn("Дать согласие", self.outbox.messages[1][1])
        generator.opener.open.assert_not_called()
        bot.message(1, "/privacy")
        self.click(bot, 1, "Дать согласие")
        bot.message(1, QUESTION)
        generator.opener.open.assert_called_once()

        restarted = self.generator(LLM_PUBLIC_USER_LIMIT="1")
        bot = self.bot(restarted)
        self.assertEqual(restarted.public_access.decision(1), "granted")
        bot.callback(1, "lang:en")
        bot.message(1, "/privacy")
        self.click(bot, 1, "Withdraw consent")
        bot.message(1, QUESTION)
        self.assertEqual(restarted.public_access.decision(1), "declined")
        restarted.opener.open.assert_not_called()
        bot.message(1, "/privacy")
        self.click(bot, 1, "Allow external AI")
        bot.message(1, QUESTION)
        self.assertEqual(restarted.last_status, "public_user")
        self.assertIn("local catalogue", self.outbox.messages[1][0])
        restarted.opener.open.assert_not_called()

    def test_language_stage_and_navigation_cannot_trigger_external_generation(self):
        generator = self.generator()
        bot = self.bot(generator)
        bot.message(1, "/privacy")
        self.click(bot, 1, "Дать согласие")
        bot.message(1, QUESTION, require_language=True)
        self.click(bot, 1, "Русский")
        self.assertIn(SOURCE.title, self.outbox.messages[1][0])
        state = bot.conversations.get(1)
        state.pending = {"kind": "clarify", "query": QUESTION}
        bot.conversations.save(1, state)
        bot.callback(1, "stage:operate")
        self.assertIn(SOURCE.title, self.outbox.messages[1][0])
        for command in ("/menu", "/help", "/reminders", "/profile", "/plan", "/step", "/privacy"):
            bot.message(1, command)
        generator.opener.open.assert_not_called()
        bot.message(1, QUESTION)
        generator.opener.open.assert_called_once()

    def test_user_rate_global_limit_and_failed_attempts_persist(self):
        first = self.generator(LLM_PUBLIC_GLOBAL_LIMIT="3", LLM_PUBLIC_USER_LIMIT="2")
        first.opener.open.side_effect = URLError("synthetic timeout")
        self.allow(first, 1)
        self.allow(first, 2)
        self.allow(first, 3)
        with patch("assistant_bot.budget.time", return_value=100.0):
            self.assertIsNone(self.generate(first))
            self.assertEqual(first.last_status, "unavailable")
            first.opener.open.assert_called_once()
            restarted = self.generator(LLM_PUBLIC_GLOBAL_LIMIT="3", LLM_PUBLIC_USER_LIMIT="2")
            self.assertIsNone(self.generate(restarted))
            self.assertEqual(restarted.last_status, "public_rate")
            restarted.opener.open.assert_not_called()
        with patch("assistant_bot.budget.time", return_value=131.0):
            self.assertIsNotNone(self.generate(restarted))
            self.assertIsNotNone(self.generate(restarted, chat=2))
            self.assertIsNone(self.generate(restarted, chat=3))
            self.assertEqual(restarted.last_status, "public_global")
            self.assertEqual(restarted.opener.open.call_count, 2)

    def test_parallel_chats_compete_for_one_persistent_attempt(self):
        generators = [self.generator(LLM_PUBLIC_GLOBAL_LIMIT="1", LLM_PUBLIC_USER_LIMIT="1") for _ in range(4)]
        barrier = Barrier(len(generators))
        for chat, generator in enumerate(generators, 1):
            self.allow(generator, chat)

        def simultaneous(pair):
            index, generator = pair
            barrier.wait(5)
            self.generate(generator, chat=index)
            return generator.last_status

        with ThreadPoolExecutor(max_workers=4) as workers:
            statuses = list(workers.map(simultaneous, enumerate(generators, 1)))
        self.assertCountEqual(statuses, ["answered", "public_global", "public_global", "public_global"])
        self.assertEqual(sum(generator.opener.open.call_count for generator in generators), 1)

    def test_concurrent_transport_is_bounded_without_allocating_busy_attempt(self):
        generator = self.generator(LLM_PUBLIC_MAX_CONCURRENT="1")
        self.allow(generator, 1)
        self.allow(generator, 2)
        entered, release = Event(), Event()

        def block(*_args, **_kwargs):
            entered.set()
            if not release.wait(5):
                raise URLError("test timeout")
            return transport_response()

        generator.opener.open.side_effect = block
        with ThreadPoolExecutor(max_workers=1) as worker:
            inflight = worker.submit(self.generate, generator)
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNone(self.generate(generator, chat=2))
                self.assertEqual(generator.last_status, "public_busy")
                generator.opener.open.assert_called_once()
            finally:
                release.set()
            self.assertIsNotNone(inflight.result(5))
        self.assertIsNotNone(self.generate(generator, chat=2))

    def test_filter_error_and_closed_evidence_never_use_budget_or_transport(self):
        generator = self.generator(LLM_PUBLIC_GLOBAL_LIMIT="1", LLM_PUBLIC_USER_LIMIT="1")
        self.allow(generator, 1)
        with patch("assistant_bot.llm.validate_outbound", side_effect=RuntimeError("filter broke")):
            self.assertIsNone(self.generate(generator))
            self.assertEqual(generator.last_status, "privacy")
        closed = Evidence(SOURCE.title, SOURCE.url, QUOTE, content_scope="closed_lesson")
        self.assertIsNone(self.generate(generator, source=closed))
        self.assertEqual(generator.last_status, "privacy")
        generator.opener.open.assert_not_called()
        self.assertIsNotNone(self.generate(generator))
        generator.opener.open.assert_called_once()

    def test_consent_is_checked_again_atomically_after_payload_validation(self):
        generator = self.generator(LLM_PUBLIC_GLOBAL_LIMIT="1", LLM_PUBLIC_USER_LIMIT="1")
        self.allow(generator, 1)
        access = generator.public_access

        def validate_and_withdraw(payload):
            validate_outbound(payload)
            self.assertTrue(access.choose(1, access.issue_notice(1), "declined"))

        with patch("assistant_bot.llm.validate_outbound", side_effect=validate_and_withdraw):
            self.assertIsNone(self.generate(generator))
        self.assertEqual(generator.last_status, "consent")
        generator.opener.open.assert_not_called()
        self.allow(generator, 1)
        self.assertIsNotNone(self.generate(generator))
        generator.opener.open.assert_called_once()

    def test_provider_or_notice_version_changes_require_new_consent_without_resetting_budget(self):
        initial = self.generator(LLM_PUBLIC_GLOBAL_LIMIT="1", LLM_PUBLIC_USER_LIMIT="1")
        self.allow(initial, 1)
        self.assertIsNotNone(self.generate(initial))
        with patch("assistant_bot.budget.PublicAccess.CONSENT_VERSION", "test-next"):
            updated = self.generator(LLM_PUBLIC_GLOBAL_LIMIT="1", LLM_PUBLIC_USER_LIMIT="1")
            self.assertIsNone(self.generate(updated))
            self.assertEqual(updated.last_status, "consent")
            updated.opener.open.assert_not_called()
        other = self.generator(LLM_PROVIDER="openrouter", OPENROUTER_API_KEY="synthetic",
                               LLM_PUBLIC_GLOBAL_LIMIT="1", LLM_PUBLIC_USER_LIMIT="1")
        self.assertIsNone(self.generate(other))
        self.assertEqual(other.last_status, "consent")
        self.allow(other, 1)
        self.assertIsNone(self.generate(other))
        self.assertEqual(other.last_status, "public_global")
        other.opener.open.assert_not_called()

    def test_closed_modality_keeps_existing_allowlist_and_budget(self):
        first = self.generator(LLM_MODE="closed", LLM_TEST_EXPIRES_AT="2099-01-01T00:00:00+00:00",
                               LLM_MAX_REQUESTS="1")
        self.assertIsNone(first.public_access)
        self.assertIsNone(self.generate(first))
        first.opener.open.assert_not_called()
        self.assertTrue(reserve_request(self.budget, first.budget_scope, 1))
        self.generator()  # Adds public tables to the existing closed-test database.
        self.assertIsNone(self.generate(first, chat=99))
        self.assertEqual(first.last_status, "budget")
        first.opener.open.assert_not_called()
        expired = ResponseGenerator("xai", "synthetic", "model", allow_case_data=True,
                                    test_chat_ids=[99], test_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
        expired.opener = Mock()
        self.assertIsNone(self.generate(expired, chat=99))
        self.assertEqual(expired.last_status, "expired")
        expired.opener.open.assert_not_called()

    def test_local_mode_and_public_catalog_permission(self):
        self.assertIsNone(generator_from_env({"LLM_MODE": "off", "LLM_PROVIDER": "xai"}))
        self.assertIsNone(generator_from_env({"LLM_MODE": "public", "LLM_PROVIDER": "none"}))
        for path in ("", ":memory:"):
            with self.assertRaisesRegex(ValueError, "LLM_BUDGET_PATH"):
                self.generator(LLM_BUDGET_PATH=path)
        public = self.generator(LLM_ALLOW_CASE_DATA="false")
        self.allow(public, 1)
        self.assertIsNone(self.generate(public))
        self.assertEqual(public.last_status, "disabled")
        public.opener.open.assert_not_called()
