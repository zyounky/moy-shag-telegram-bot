import threading
import unittest

from assistant_bot.dispatch import ChatDispatcher


def update(chat, identifier):
    return {'update_id': identifier, 'message': {'chat': {'id': chat, 'type': 'private'}, 'text': 'Вопрос'}}


class DispatchTests(unittest.TestCase):
    def test_other_chat_is_responsive_and_same_chat_keeps_order(self):
        started, release, other = threading.Event(), threading.Event(), threading.Event()
        seen = []

        def handler(item):
            if item['update_id'] == 1:
                started.set()
                release.wait(3)
            seen.append(item['update_id'])
            if item['update_id'] == 3:
                other.set()

        dispatcher = ChatDispatcher(handler, workers=2)
        try:
            self.assertTrue(dispatcher.submit(update(1, 1)))
            self.assertTrue(started.wait(1))
            self.assertTrue(dispatcher.submit(update(1, 2)))
            self.assertTrue(dispatcher.submit(update(2, 3)))
            self.assertTrue(other.wait(1))
            self.assertNotIn(2, seen)
        finally:
            release.set()
            dispatcher.close()
        self.assertLess(seen.index(1), seen.index(2))

    def test_backlog_is_bounded_and_shutdown_drains_accepted_updates(self):
        started, release = threading.Event(), threading.Event()
        seen = []

        def handler(item):
            started.set()
            release.wait(3)
            seen.append(item['update_id'])

        dispatcher = ChatDispatcher(handler, workers=1, capacity=2, per_chat=2)
        try:
            self.assertTrue(dispatcher.submit(update(1, 1)))
            self.assertTrue(started.wait(1))
            self.assertTrue(dispatcher.submit(update(1, 2)))
            self.assertFalse(dispatcher.submit(update(1, 3)))
            self.assertFalse(dispatcher.submit(update(2, 4)))
        finally:
            release.set()
            dispatcher.close()
        self.assertEqual(seen, [1, 2])
        self.assertFalse(dispatcher.submit(update(1, 5)))

    def test_failed_update_does_not_break_the_next_one(self):
        seen = []

        def handler(item):
            seen.append(item['update_id'])
            if item['update_id'] == 1:
                raise ValueError('synthetic error')

        dispatcher = ChatDispatcher(handler, workers=1)
        dispatcher.submit(update(1, 1))
        dispatcher.submit(update(1, 2))
        dispatcher.close()
        self.assertEqual(seen, [1, 2])
