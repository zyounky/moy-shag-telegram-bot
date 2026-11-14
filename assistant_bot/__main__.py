import argparse
import logging
import os
import re
import sys
import threading
from pathlib import Path
from time import sleep

from .bot import Assistant
from .knowledge import Catalog
from .reminders import ReminderStore
from .rerank import LocalReranker
from .state import ConversationStore
from .telegram import Telegram, TelegramError


LOG = logging.getLogger(__name__)
DEMO_CATALOG = Path(__file__).with_name("demo_catalog.jsonl")


class Console:
    def send(self, chat, message, rows=None):
        from html import unescape

        print(unescape(re.sub(r"</?(b|i|code)>", "", message)))
        if rows:
            print("  ".join(
                "[" + action["text"] + "] " + action.get("url", f"/click {action.get('callback_data', '')}")
                for row in rows for action in row
            ))
        print()


def make_assistant(api, catalog, state_path, chooser=None):
    return Assistant(api, catalog, ConversationStore(state_path), ReminderStore(state_path), chooser=chooser)


def run(api, assistant):
    identity = api.call("getMe")
    LOG.info("Бот @%s готов", identity["username"])
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                assistant.deliver_reminders()
            except Exception as error:
                LOG.error("Ошибка обработчика напоминаний: %s", type(error).__name__)
            stop.wait(2)

    reminders = threading.Thread(target=worker, name="reminders", daemon=True)
    reminders.start()
    offset = None
    try:
        while True:
            try:
                updates = api.poll(offset)
            except TelegramError as error:
                if error.code in (401, 404, 409):
                    raise
                LOG.warning("%s", error)
                sleep(max(2, error.retry_after))
                continue
            for update in updates:
                try:
                    assistant.update(update)
                except Exception as error:
                    LOG.error("Обновление %s: %s", update["update_id"], type(error).__name__)
                finally:
                    offset = update["update_id"] + 1
    finally:
        stop.set()
        reminders.join(timeout=12)


def main():
    parser = argparse.ArgumentParser(description="Помощник «Альфа-Курса» для Telegram")
    parser.add_argument("--check", action="store_true", help="проверить загрузку каталога и SQLite")
    parser.add_argument("--demo", action="store_true", help="локальный диалог с учебными данными")
    parser.add_argument("--console", action="store_true", help="локальный диалог с указанным каталогом")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    demo_mode = args.demo or os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true")
    catalog_path = DEMO_CATALOG if demo_mode else Path(os.getenv("CATALOG_PATH", "data/catalog.jsonl"))
    state_path = Path(os.getenv("STATE_PATH", "runtime/state.db"))
    token = os.getenv("BOT_TOKEN", "").strip()
    model_url = os.getenv("OLLAMA_URL", "").strip()

    try:
        catalog = Catalog(catalog_path)
        chooser = LocalReranker(model_url, os.getenv("OLLAMA_MODEL", "").strip()) if model_url else None
        if args.check:
            make_assistant(Console(), catalog, state_path)
            print(f"Каталог и SQLite готовы. Статьи: {catalog.article_count}, материалы: {catalog.material_count}.")
            return 0
        if args.demo or args.console:
            assistant = make_assistant(Console(), catalog, state_path, chooser)
            assistant.message(1, "/start")
            for entry in sys.stdin:
                text = entry.strip()
                if text.startswith("/click "):
                    assistant.callback(1, text.removeprefix("/click "))
                elif text:
                    assistant.message(1, text)
                assistant.deliver_reminders()
            return 0
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
            LOG.error("Укажите токен Telegram в BOT_TOKEN. Для локального просмотра используйте --console.")
            return 2
        api = Telegram(token)
        run(api, make_assistant(api, catalog, state_path, chooser))
    except (OSError, ValueError, TelegramError) as error:
        LOG.error("%s", error)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
