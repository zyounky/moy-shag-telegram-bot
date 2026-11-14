import argparse
import json
import logging
import os
import re
import sys
import threading
from pathlib import Path
from time import sleep

from .bot import Assistant
from .knowledge import Catalog
from .llm import generator_from_env
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


def make_assistant(api, catalog, state_path, chooser=None, generator=None):
    return Assistant(api, catalog, ConversationStore(state_path), ReminderStore(state_path),
                     chooser=chooser, generator=generator)


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
    parser.add_argument("--llm-check", action="store_true", help="один реальный запрос LLM на учебных данных")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    demo_mode = args.demo or os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true")
    catalog_path = DEMO_CATALOG if demo_mode else Path(os.getenv("CATALOG_PATH", "data/catalog.jsonl"))
    state_path = Path(os.getenv("STATE_PATH", "runtime/state.db"))
    token = os.getenv("BOT_TOKEN", "").strip()
    model_url = os.getenv("OLLAMA_URL", "").strip()

    try:
        if args.llm_check:
            generator = generator_from_env(os.environ)
            if not generator:
                raise ValueError("Для --llm-check задайте LLM_PROVIDER и ключ выбранного сервиса.")
            demo = Catalog(DEMO_CATALOG)
            question = "Как оценить эффективность рекламы кофейни?"
            result = generator.generate(question, {}, demo.search(question).evidence, is_demo=True)
            if not result:
                LOG.error("Проверка LLM не пройдена. Проверьте ключ, доступ к модели, баланс и сеть.")
                return 2
            print(f"LLM готова: {generator.provider} / {generator.model}. Структура и цитата проверены.")
            return 0
        catalog = Catalog(catalog_path)
        chooser = LocalReranker(model_url, os.getenv("OLLAMA_MODEL", "").strip()) if model_url else None
        if args.check:
            make_assistant(Console(), catalog, state_path)
            print(f"Каталог и SQLite готовы. Материалы: {catalog.material_count}; с текстом: {catalog.text_material_count}.")
            print("Типы: " + json.dumps(catalog.kind_counts, ensure_ascii=False))
            return 0
        generator = generator_from_env(os.environ)
        if generator:
            LOG.info("LLM: %s / %s; данные кейса: %s", generator.provider, generator.model,
                     "разрешены настройкой" if generator.allow_case_data else "не отправляются")
        if args.demo or args.console:
            assistant = make_assistant(Console(), catalog, state_path, chooser, generator)
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
        run(api, make_assistant(api, catalog, state_path, chooser, generator))
    except (OSError, ValueError, TelegramError) as error:
        LOG.error("%s", error)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
