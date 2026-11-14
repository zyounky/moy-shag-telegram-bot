"""Non-blocking Telegram previews; action buttons appear only in the final reply."""

import logging
import threading
from html import escape


LOG = logging.getLogger(__name__)


class DraftReply:
    def __init__(self, api, chat):
        self.api = api
        self.chat = chat
        self.message_id = None
        self._latest = ""
        self._sent = ""
        self._state = threading.Lock()
        self._io = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def update(self, text):
        with self._state:
            self._latest = text
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="answer-preview")
                self._thread.start()
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(1.5)
            self._wake.clear()
            if self._stop.is_set():
                return
            with self._state:
                text = self._latest
            if not text or text == self._sent:
                continue
            preview = "<i>Ответ формируется…</i>\n\n" + escape(text, quote=False)
            try:
                with self._io:
                    if self._stop.is_set():
                        return
                    if self.message_id is None:
                        result = self.api.send(self.chat, preview, request_timeout=2)
                        self.message_id = result["message_id"]
                    else:
                        self.api.edit(self.chat, self.message_id, preview, request_timeout=2)
                    self._sent = text
            except Exception:
                LOG.info("Предварительный ответ временно недоступен")
            self._stop.wait(1.5)

    def finish(self, text, rows=None):
        self._stop.set()
        self._wake.set()
        # A preview already in flight must finish before the final edit.
        with self._io:
            if self.message_id is not None:
                try:
                    return self.api.edit(self.chat, self.message_id, text, rows)
                except Exception:
                    LOG.info("Финальный ответ будет отправлен отдельным сообщением")
            return self.api.send(self.chat, text, rows)
