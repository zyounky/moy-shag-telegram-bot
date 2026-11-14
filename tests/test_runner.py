import signal
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from assistant_bot.__main__ import run


class RunnerTests(unittest.TestCase):
    def test_poll_dispatches_other_chat_and_acknowledges_callback_before_work(self):
        release = threading.Event()
        fast_done = threading.Event()
        observations = []
        api = Mock()
        api.call.return_value = {'username': 'synthetic_test_bot'}
        updates = [
            {'update_id': 1, 'message': {'chat': {'id': 1, 'type': 'private'}, 'text': 'Вопрос'}},
            {'update_id': 2, 'callback_query': {'id': 'callback', 'message':
                                               {'chat': {'id': 2, 'type': 'private'}}, 'data': 'menu:help'}},
        ]

        def handler(item):
            if item['update_id'] == 1:
                observations.append(('slow_released', release.wait(2)))
            else:
                observations.append(('callback_acknowledged', item.get('_callback_acknowledged')))
                release.set()
                fast_done.set()

        def final_poll(offset):
            fast_done.wait(2)
            raise KeyboardInterrupt

        first = True

        def poll(offset):
            nonlocal first
            if first:
                first = False
                return updates
            return final_poll(offset)

        api.poll.side_effect = poll
        previous = signal.getsignal(signal.SIGTERM)
        try:
            with self.assertRaises(KeyboardInterrupt):
                run(api, SimpleNamespace(update=handler, deliver_reminders=lambda: None))
        finally:
            release.set()
        self.assertIn(('slow_released', True), observations)
        self.assertIn(('callback_acknowledged', True), observations)
        api.answer_callback.assert_called_once_with('callback')
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
