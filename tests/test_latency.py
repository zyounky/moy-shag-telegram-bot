import json
import threading
import unittest
from io import BytesIO
from time import monotonic
from unittest.mock import Mock

from assistant_bot.knowledge import Evidence
from assistant_bot.llm import ResponseGenerator, answer_preview, quote_options
from assistant_bot.progress import DraftReply


QUOTE = "Разделите расходы на количество заказов, чтобы оценить стоимость одного заказа."
SOURCE = Evidence("Результат рекламы", "https://kurs.alfabank.ru/articles/reklama/", QUOTE)


def result():
    return {"status": "answered", "source_index": 0, "source_indices": [0], "quote": QUOTE,
            "answer": "Стоимость заказа — это расходы на рекламу, разделённые на количество заказов.",
            "action": "Выберите одну кампанию и запишите расходы и число заказов.",
            "completion": "Стоимость заказа посчитана за один период."}


def envelope(value):
    return {"status": "completed", "output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": json.dumps(value, ensure_ascii=False)}]}]}


class Stream(BytesIO):
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}


def stream_for(value, complete=True):
    text = json.dumps(value, ensure_ascii=False)
    events = [{"type": "response.output_text.delta", "delta": text[i:i+17]}
              for i in range(0, len(text), 17)]
    if complete:
        events.append({"type": "response.completed", "response": envelope(value)})
    return Stream("".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events).encode())


class LatencyTests(unittest.TestCase):
    def generator(self):
        generator = ResponseGenerator("xai", "test", "grok-4.7")
        generator.opener = Mock()
        return generator

    def test_stream_and_json_receive_the_same_final_validation(self):
        generator = self.generator()
        generator.opener.open.return_value = stream_for(result())
        previews = []
        generated = generator.generate("Реклама", {}, (SOURCE,), is_demo=True, on_preview=previews.append)
        self.assertEqual(generated[1].answer, result()["answer"])
        self.assertTrue(previews)
        self.assertTrue(all(result()["answer"].startswith(p) for p in previews))
        self.assertIn("first_answer_seconds", generator.last_metrics)

    def test_unverified_anchor_never_reaches_preview(self):
        generator = self.generator()
        generator.opener.open.return_value = stream_for({**result(), "quote": "Придуманная цитата, отсутствующая в источнике."})
        previews = []
        self.assertIsNone(generator.generate("Реклама", {}, (SOURCE,), is_demo=True, on_preview=previews.append))
        self.assertEqual(previews, [])

    def test_interrupted_stream_is_not_cached_or_accepted(self):
        generator = self.generator()
        generator.opener.open.return_value = stream_for(result(), complete=False)
        self.assertIsNone(generator.generate("Реклама", {}, (SOURCE,), is_demo=True))
        self.assertEqual(len(generator._cache), 0)

    def test_preview_decodes_escapes_without_showing_json_or_reasoning(self):
        value = {**result(), "answer": "Первое предложение.\nВторое содержит «пример» и кавычки: \"да\"."}
        text = json.dumps(value, ensure_ascii=True)
        for cut in range(len(text)):
            preview = answer_preview(text[:cut], (SOURCE,))
            if preview:
                self.assertTrue(value["answer"].startswith(preview))

    def test_cache_is_exact_scoped_and_invalidated_by_context(self):
        generator = self.generator()
        generator.opener.open.side_effect = lambda *a, **k: BytesIO(json.dumps(envelope(result())).encode())
        first = generator.generate("Реклама", {}, (SOURCE,), is_demo=True, chat_id=1)
        cached = generator.generate("Реклама", {}, (SOURCE,), is_demo=True, chat_id=1)
        self.assertEqual(first, cached)
        self.assertTrue(generator.last_metrics["cache_hit"])
        self.assertEqual(generator.opener.open.call_count, 1)
        generator.generate("Реклама", {"stage": "launch"}, (SOURCE,), is_demo=True, chat_id=1)
        generator.generate("Реклама", {}, (SOURCE,), is_demo=True, chat_id=2)
        self.assertEqual(generator.opener.open.call_count, 3)
        for key, (_, value) in list(generator._cache.items()):
            generator._cache[key] = (monotonic() - 301, value)
        generator.generate("Реклама", {}, (SOURCE,), is_demo=True, chat_id=1)
        self.assertEqual(generator.opener.open.call_count, 4)

    def test_quote_options_are_exact_source_spans(self):
        for quote in quote_options("Реклама", QUOTE):
            self.assertIn(quote, QUOTE)
            self.assertGreaterEqual(len(quote), 35)

    def test_preview_cannot_overwrite_final_message_and_has_no_action_buttons(self):
        class API:
            def __init__(self):
                self.messages = []
                self.ready = threading.Event()

            def send(self, chat, text, rows=None, **kwargs):
                self.messages.append((text, rows))
                self.ready.set()
                return {"message_id": 10}

            def edit(self, chat, message_id, text, rows=None, **kwargs):
                self.messages.append((text, rows))
                return {"message_id": message_id}

        api = API()
        reply = DraftReply(api, 1)
        reply.update("Короткая предварительная часть ответа.")
        self.assertTrue(api.ready.wait(1))
        buttons = [[{"text": "Взять шаг", "callback_data": "take"}]]
        reply.finish("Проверенный итоговый ответ", buttons)
        reply.update("Опоздавшее обновление")
        reply._thread.join(1)
        self.assertEqual(api.messages[-1], ("Проверенный итоговый ответ", buttons))
        self.assertIsNone(api.messages[0][1])
