import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from assistant_bot.bot import Assistant
from assistant_bot.knowledge import Catalog, DEMO_CATALOG, Evidence
from assistant_bot.llm import ResponseGenerator, generator_from_env
from assistant_bot.reminders import ReminderStore
from assistant_bot.state import ConversationStore
from assistant_bot.__main__ import main


QUOTE = "Разделите расходы на количество заказов, чтобы оценить стоимость одного заказа."
EVIDENCE = Evidence("Результат рекламы", "https://kurs.alfabank.ru/articles/reklama/", QUOTE)


def valid_result():
    return {"status": "answered", "source_index": 0, "source_indices": [0], "quote": QUOTE,
            "answer": "Стоимость заказа можно оценить по расходам и числу заказов.",
            "action": "Разделите расходы на рекламу на число заказов за тот же период.",
            "completion": "Записаны расходы, число заказов и стоимость одного заказа."}


def response_for(result, status="completed"):
    return BytesIO(json.dumps({"status": status, "output": [
        {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": json.dumps(result, ensure_ascii=False)}]}]
    }).encode())


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.generator = ResponseGenerator("xai", "test-key", "grok-4.7")
        self.generator.opener = Mock()

    def generate(self, profile=None, **kwargs):
        return self.generator.generate("Как оценить рекламу?", profile or {}, (EVIDENCE,), **kwargs)

    def test_generates_advice_using_only_catalog_source_and_bounded_context(self):
        self.generator.opener.open.return_value = response_for(valid_result())
        source, advice = self.generate({"stage": "operate", "chat_id": 123,
                                       "name": "Личное имя", "timezone": "Europe/Moscow"}, is_demo=True)
        self.assertEqual(source, EVIDENCE)
        self.assertEqual(advice.url, EVIDENCE.url)
        self.assertEqual(advice.answer, valid_result()["answer"])
        request = self.generator.opener.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertFalse(body["store"])
        self.assertNotIn("tools", body)
        inputs = json.loads(body["input"][1]["content"])
        self.assertEqual(inputs["context"], {"stage": "operate"})
        self.assertNotIn(EVIDENCE.url, request.data.decode())

    def test_private_case_catalog_is_not_sent_without_explicit_setting(self):
        self.assertIsNone(self.generate())
        self.generator.opener.open.assert_not_called()
        self.generator.allow_case_data = True
        self.generator.opener.open.return_value = response_for(valid_result())
        self.assertIsNotNone(self.generate())

    def test_english_advice_preserves_the_original_quote(self):
        result = {**valid_result(), "answer": "Divide ad spend by the number of orders.",
                  "action": "Divide ad spend by orders for the same period.",
                  "completion": "Record the spend, orders, and cost per order."}
        self.generator.opener.open.return_value = response_for(result)
        _, advice = self.generate(is_demo=True, language="en")
        prompt = json.loads(self.generator.opener.open.call_args.args[0].data)["input"][0]["content"]
        self.assertIn("на английском", prompt)
        self.assertEqual(advice.passage, QUOTE)
        self.assertEqual(advice.action, result["action"])

    def test_obvious_contacts_and_keys_are_removed_before_request(self):
        self.generator.opener.open.return_value = response_for(valid_result())
        self.generator.generate("Моя почта person@example.org, телефон +7 (999) 123-45-67",
                                {}, (EVIDENCE,), obstacle="xai-123456789secret", is_demo=True)
        content = self.generator.opener.open.call_args.args[0].data.decode()
        for secret in ("person@example.org", "+7 (999) 123-45-67", "xai-123456789secret"):
            self.assertNotIn(secret, content)

    def test_invalid_or_untrusted_outputs_fall_back(self):
        invalid = [{**valid_result(), "source_index": True},
                   {**valid_result(), "source_index": 9},
                   {**valid_result(), "quote": "Выдуманная цитата, которой в материале нет."},
                   {**valid_result(), "answer": "Смотрите https://example.org/"},
                   {**valid_result(), "action": "<b>Сделайте</b>"},
                   {**valid_result(), "completion": ""},
                   {**valid_result(), "answer": "я" * 1601},
                   {**valid_result(), "source_indices": [0, 9]},
                   {**valid_result(), "source_indices": [0, 0]},
                   {**valid_result(), "extra": "unexpected"},
                   {**valid_result(), "status": "insufficient"}]
        for result in invalid:
            with self.subTest(result=result):
                self.generator.opener.open.return_value = response_for(result)
                self.assertIsNone(self.generate(is_demo=True))

    def test_timeout_http_errors_and_incomplete_response_do_not_break_bot(self):
        for error in (TimeoutError(), URLError("offline"),
                      HTTPError("https://api.x.ai", 429, "limited", {}, None)):
            self.generator.opener.open.side_effect = error
            self.assertIsNone(self.generate(is_demo=True))
        self.generator.opener.open.side_effect = None
        self.generator.opener.open.return_value = response_for(valid_result(), "incomplete")
        self.assertIsNone(self.generate(is_demo=True))
        self.generator.opener.open.return_value = BytesIO(b'{"status":"completed","output":null}')
        self.assertIsNone(self.generate(is_demo=True))

    def test_no_evidence_means_no_request(self):
        self.assertIsNone(self.generator.generate("Вопрос", {}, (), is_demo=True))
        self.generator.opener.open.assert_not_called()

    def test_multiple_sources_are_validated_and_mapped_to_catalog_links(self):
        second = Evidence("Второй материал", "https://kurs.alfabank.ru/articles/second/", QUOTE)
        result = {**valid_result(), "source_indices": [0, 1]}
        self.generator.opener.open.return_value = response_for(result)
        generated = self.generator.generate("Реклама", {}, (EVIDENCE, second), is_demo=True)
        self.assertEqual(generated[1].sources, ((EVIDENCE.title, EVIDENCE.url), (second.title, second.url)))

    def test_insufficient_answer_is_distinct_from_network_failure(self):
        result = {**valid_result(), "status": "insufficient", "source_indices": [],
                  "answer": "Какой показатель вы хотите оценить?", "quote": "", "action": "", "completion": ""}
        self.generator.opener.open.return_value = response_for(result)
        self.assertIsNone(self.generate(is_demo=True))
        self.assertEqual(self.generator.last_status, "insufficient")
        self.assertIn("показатель", self.generator.last_clarification)

    def test_case_telegram_requests_require_tester_and_unexpired_session(self):
        self.generator.allow_case_data = True
        self.generator.opener.open.return_value = response_for(valid_result())
        self.assertIsNone(self.generate(chat_id=123))
        self.generator.test_chat_ids = frozenset({123})
        self.assertIsNone(self.generate(chat_id=123))
        self.generator.test_expires_at = datetime.now(UTC) + timedelta(hours=1)
        self.assertIsNone(self.generate(chat_id=456))
        self.generator.opener.open.assert_not_called()
        self.assertIsNotNone(self.generate(chat_id=123))
        self.generator.test_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        self.assertIsNone(self.generate(chat_id=123))
        self.assertEqual(self.generator.opener.open.call_count, 1)

    def test_request_budget_includes_failed_requests(self):
        self.generator.remaining_requests = 1
        self.generator.opener.open.side_effect = TimeoutError()
        self.assertIsNone(self.generate(is_demo=True))
        self.assertIsNone(self.generate(is_demo=True))
        self.assertEqual(self.generator.opener.open.call_count, 1)

    def test_provider_configuration_and_openrouter_route(self):
        self.assertIsNone(generator_from_env({}))
        with self.assertRaises(ValueError):
            generator_from_env({"LLM_PROVIDER": "xai"})
        with self.assertRaises(ValueError):
            generator_from_env({"LLM_PROVIDER": "unknown"})
        with self.assertRaises(ValueError):
            generator_from_env({"LLM_PROVIDER": "xai", "XAI_API_KEY": "test", "LLM_TIMEOUT_SECONDS": "0"})
        generator = generator_from_env({"LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "test"})
        self.assertEqual(generator.model, "anthropic/claude-sonnet-5.5")
        generator.opener = Mock()
        generator.opener.open.return_value = response_for(valid_result())
        self.assertIsNotNone(generator.generate("Реклама", {}, (EVIDENCE,), is_demo=True))
        request = generator.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/responses")
        self.assertFalse(json.loads(request.data)["provider"]["allow_fallbacks"])

    def test_api_check_uses_synthetic_catalog_without_runtime_files_or_telegram_token(self):
        fake = Mock(provider="xai", model="grok-4.7")
        fake.generate.return_value = (EVIDENCE, object())
        with patch("sys.argv", ["assistant_bot", "--llm-check"]), \
                patch("assistant_bot.__main__.generator_from_env", return_value=fake), \
                redirect_stdout(StringIO()):
            self.assertEqual(main(), 0)
        self.assertTrue(fake.generate.call_args.kwargs["is_demo"])
        fake.generate.return_value = None
        with patch("sys.argv", ["assistant_bot", "--llm-check"]), \
                patch("assistant_bot.__main__.generator_from_env", return_value=fake):
            self.assertEqual(main(), 2)

    def test_bot_keeps_generated_step_and_canonical_link_and_handles_api_failure(self):
        class Outbox:
            def __init__(self):
                self.messages = []

            def send(self, chat, text, rows=None):
                self.messages.append((text, rows))

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.db"
            state = ConversationStore(path)
            outbox = Outbox()
            catalog = Catalog(DEMO_CATALOG)
            query = "Как оценить эффективность рекламы кофейни?"
            source = catalog.search(query).evidence[0]
            result = valid_result()
            result["quote"] = source.passage[:100]
            self.generator.opener.open.return_value = response_for(result)
            bot = Assistant(outbox, catalog, state, ReminderStore(path),
                            now=lambda: datetime(2026, 11, 14, 12, tzinfo=UTC), generator=self.generator)
            bot.message(1, query)
            self.assertIn("По вашей задаче", outbox.messages[-1][0])
            self.assertEqual(state.get(1).proposal["title"], result["action"])
            self.assertEqual(state.get(1).proposal["completion"], result["completion"])
            self.generator._cache.clear()
            self.generator.opener.open.side_effect = TimeoutError()
            bot.message(1, query)
            self.assertIn("Не удалось подготовить ответ", outbox.messages[-1][0])
            self.assertNotIn("По вашей задаче", outbox.messages[-1][0])
            self.assertNotIn("Что нашёл", outbox.messages[-1][0])
