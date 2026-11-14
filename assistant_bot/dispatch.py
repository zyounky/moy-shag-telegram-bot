"""Bounded concurrent chats, with ordered processing inside each chat."""

import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Semaphore


LOG = logging.getLogger(__name__)


def private_chat_id(update):
    message = update.get("callback_query", {}).get("message") or update.get("message", {})
    chat = message.get("chat", {})
    return chat.get("id") if chat.get("type") == "private" else None


class ChatDispatcher:
    def __init__(self, handler, *, workers=4, capacity=8, per_chat=2):
        self.handler = handler
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chat")
        self._slots = Semaphore(capacity)
        self._lock = Lock()
        self._queues = {}
        self._counts = {}
        self._per_chat = per_chat
        self._closed = False

    def submit(self, update):
        chat = private_chat_id(update)
        if chat is None:
            return True
        with self._lock:
            if self._closed or self._counts.get(chat, 0) >= self._per_chat:
                return False
            if not self._slots.acquire(blocking=False):
                return False
            first = chat not in self._queues
            queue = self._queues.setdefault(chat, deque())
            queue.append(update)
            self._counts[chat] = self._counts.get(chat, 0) + 1
            if first:
                self._executor.submit(self._drain, chat)
            return True

    def _drain(self, chat):
        while True:
            with self._lock:
                queue = self._queues[chat]
                if not queue:
                    del self._queues[chat]
                    del self._counts[chat]
                    return
                update = queue.popleft()
            try:
                self.handler(update)
            except Exception as error:
                LOG.error("Обновление %s: %s", update.get("update_id"), type(error).__name__)
            finally:
                with self._lock:
                    self._counts[chat] -= 1
                self._slots.release()

    def close(self):
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True)
