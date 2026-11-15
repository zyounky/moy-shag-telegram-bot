import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from assistant_bot.bot import Assistant, continued_question
from assistant_bot.knowledge import Evidence, Material, SearchResult
from assistant_bot.privacy import has_personal_disclosure
from assistant_bot.reminders import ReminderStore
from assistant_bot.state import ConversationStore


class API:
    def __init__(self):
        self.messages = []

    def send(self, chat, text, rows=None):
        self.messages.append((chat, text, rows or []))


class ReleaseRegressionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'state.db'
        self.store = ConversationStore(self.path)
        self.source = Evidence('Результат рекламы', 'https://kurs.alfabank.ru/articles/ad/',
                               'Сравните затраты и число заказов за одинаковые периоды.')
        self.paid = Material('Курс рекламы', 'https://kurs.alfabank.ru/courses/ad/', 'course',
                             '1 000 ₽', 'Курс по настройке рекламы.', 'Для углублённой работы.', 1000, 'paid')
        self.search = Mock(return_value=SearchResult((self.source,), (self.paid,)))
        self.generator = SimpleNamespace(last_status='insufficient', last_metrics={},
                                         last_clarification='В каком канале размещается реклама?',
                                         generate=Mock(return_value=None))
        self.api = API()
        self.bot = Assistant(self.api, SimpleNamespace(is_demo=False, search=self.search),
                             self.store, ReminderStore(self.path), generator=self.generator)

    def test_llm_clarifications_keep_question_across_restart_and_are_chat_scoped(self):
        question = 'Как оценить эффективность рекламы?'
        self.bot.message(1, question)
        self.assertEqual(self.store.get(1).pending['kind'], 'clarify')
        self.bot.conversations = ConversationStore(self.path)
        self.generator.last_clarification = 'Что хотите измерить?'
        self.bot.message(1, 'ВКонтакте')
        self.assertIn(question, self.search.call_args.args[0])
        self.assertIn('ВКонтакте', self.search.call_args.args[0])
        self.bot.message(1, 'Цель — заказы')
        self.assertIn('ВКонтакте', self.search.call_args.args[0])
        self.assertIn('заказы', self.search.call_args.args[0])
        self.bot.message(2, 'Что такое NPS?')
        self.assertNotIn('ВКонтакте', self.search.call_args.args[0])

    def test_repeated_clarification_ends_with_sources(self):
        self.bot.message(1, 'Как оценить эффективность рекламы?')
        self.generator.last_clarification = 'В каком КАНАЛЕ размещается реклама !'
        self.bot.message(1, 'ВКонтакте')
        self.assertFalse(self.store.get(1).pending)
        _, message, rows = self.api.messages[-1]
        self.assertNotIn('КАНАЛЕ', message)
        self.assertIn(self.source.url, [item.get('url') for row in rows for item in row])

    def test_general_tax_question_asks_for_tax_regime_without_model(self):
        self.search.return_value = SearchResult(needs_clarification=True)
        self.bot.message(1, 'Как платить налоги?')
        self.generator.generate.assert_not_called()
        self.assertIn('налоговый режим', self.api.messages[-1][1])

    def test_explicit_new_question_and_cancel_leave_old_clarification(self):
        self.bot.message(1, 'Как оценить эффективность рекламы?')
        self.bot.message(1, 'Как нанять сотрудника?')
        self.assertEqual(self.search.call_args.args[0], 'Как нанять сотрудника?')
        self.bot.message(1, '/cancel')
        self.assertEqual(self.store.get(1).pending, {})

    def test_latest_clarification_is_not_lost_when_input_is_long(self):
        combined = continued_question('Как оценить рекламу?' + ' контекст' * 100, 'ВКонтакте: ' + 'данные ' * 100 + 'последний факт')
        self.assertLessEqual(len(combined), 750)
        self.assertTrue(combined.startswith('Как оценить рекламу?'))
        self.assertTrue(combined.endswith('последний факт'))

    def test_paid_material_survives_all_generator_failures_and_links_are_unique(self):
        for status in ['unavailable', 'disabled', 'expired', 'budget', 'insufficient']:
            with self.subTest(status=status):
                self.generator.last_status = status
                self.bot.message(1, '/cancel')
                self.bot.message(1, 'Как оценить эффективность рекламы?')
                rows = self.api.messages[-1][2]
                urls = [b['url'] for row in rows for b in row if 'url' in b]
                self.assertIn(self.paid.url, urls)
                self.assertIn(self.source.url, urls)
                self.assertEqual(len(urls), len(set(urls)))
                self.assertFalse(any(b.get('callback_data', '').startswith('action:take') for row in rows for b in row))

    def test_obvious_personal_input_does_not_change_context_or_call_generator(self):
        state = self.store.get(1)
        state.topic = 'Исходная задача'
        state.pending = {'kind': 'clarify', 'query': 'Исходная задача'}
        self.store.save(1, state)
        self.bot.message(1, 'Меня зовут Иван Иванов, адрес: улица Ленина, дом 10')
        self.generator.generate.assert_not_called()
        self.assertEqual(self.store.get(1).topic, state.topic)
        self.assertEqual(self.store.get(1).pending, state.pending)
        self.assertIn('персональных данных', self.api.messages[-1][1])
        for safe in ['Как получить ИНН?', 'Как настроить Яндекс Директ?', 'Что такое паспорт сделки?']:
            self.assertFalse(has_personal_disclosure(safe))

    def test_state_from_deployed_and_main_versions_preserves_both_fields(self):
        variants = [dict(topic='Исходная тема'), dict(language='en'), dict(topic='Тема', language='ru')]
        for chat, extra in enumerate(variants, 1):
            payload = dict(profile={'stage': 'operate'}, pending={}, action={'id': 'kept'}, proposal={}, **extra)
            with closing(self.store.connect()) as connection, connection:
                connection.execute('INSERT INTO conversations VALUES (?, ?)', (chat, json.dumps(payload)))
            state = self.store.get(chat)
            self.store.save(chat, state)
            reopened = ConversationStore(self.path).get(chat)
            self.assertEqual(reopened.action, {'id': 'kept'})
            self.assertEqual(reopened.topic, extra.get('topic', ''))
            self.assertEqual(reopened.language, extra.get('language', ''))
