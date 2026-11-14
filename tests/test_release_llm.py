import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import Mock

from assistant_bot.knowledge import Evidence
from assistant_bot.llm import ResponseGenerator


QUOTE = 'Разделите расходы на рекламу на число заказов за один и тот же период.'
SOURCE = Evidence('Стоимость заказа', '', QUOTE)


def response(insufficient=False):
    value = {'status': 'insufficient' if insufficient else 'answered', 'source_index': 0,
             'source_indices': [] if insufficient else [0], 'quote': '' if insufficient else QUOTE,
             'answer': 'Уточните канал рекламы?' if insufficient else 'Разделите расходы на число заказов.',
             'action': '' if insufficient else 'Запишите расходы и заказы.',
             'completion': '' if insufficient else 'Показатель посчитан.'}
    envelope = {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}
    return BytesIO(json.dumps(envelope).encode())


class ConcurrentGenerationTests(unittest.TestCase):
    def test_status_and_clarification_are_isolated_between_workers(self):
        generator = ResponseGenerator('xai', 'test', 'model')
        barrier = threading.Barrier(2)
        finished = threading.Barrier(2)

        def open_request(request, **kwargs):
            question = json.loads(json.loads(request.data)['input'][1]['content'])['question']
            barrier.wait(timeout=2)
            return response(question == 'Первый')

        generator.opener = Mock()
        generator.opener.open.side_effect = open_request

        def work(question):
            result = generator.generate(question, {}, (SOURCE,), is_demo=True)
            finished.wait(timeout=2)
            return bool(result), generator.last_status, generator.last_clarification

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(work, ['Первый', 'Второй']))
        self.assertEqual(first, (False, 'insufficient', 'Уточните канал рекламы?'))
        self.assertEqual(second, (True, 'answered', ''))

    def test_parallel_requests_cannot_overspend_in_memory_budget(self):
        generator = ResponseGenerator('xai', 'test', 'model', max_requests=1)
        generator.opener = Mock()
        generator.opener.open.side_effect = lambda *a, **k: response()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: generator.generate(str(i), {}, (SOURCE,), is_demo=True), range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(generator.opener.open.call_count, 1)

    def test_personal_disclosures_are_stopped_before_api_and_budget(self):
        generator = ResponseGenerator('xai', 'test', 'model')
        generator.opener = Mock()
        for text in ['Меня зовут Иван Иванов', 'Адрес: улица Ленина, дом 10', 'СНИЛС: 123-456-789 00']:
            with self.subTest(text=text):
                self.assertIsNone(generator.generate(text, {}, (SOURCE,), is_demo=True))
                self.assertEqual(generator.last_status, 'privacy')
        generator.opener.open.assert_not_called()
        self.assertEqual(generator.remaining_requests, 30)
