import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramError(RuntimeError):
    def __init__(self, code: int, method: str, retry_after: int = 0):
        super().__init__(f"Telegram API {method}: {code}")
        self.code = code
        self.retry_after = retry_after


class Telegram:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method, *, request_timeout=10, **payload):
        request = Request(
            self.base + method, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=request_timeout) as response:
                data = json.load(response)
        except HTTPError as error:
            try:
                details = json.load(error)
                retry_after = details.get("parameters", {}).get("retry_after", 0)
            except (ValueError, OSError):
                retry_after = 0
            raise TelegramError(error.code, method, retry_after) from None
        except (URLError, OSError, ValueError):
            raise TelegramError(0, method) from None
        if not data["ok"]:
            raise TelegramError(data.get("error_code", 0), method)
        return data.get("result")

    def poll(self, offset: int | None):
        return self.call(
            "getUpdates", request_timeout=25, timeout=15, offset=offset, limit=20,
            allowed_updates=["message", "callback_query"],
        )

    def send(self, chat_id, text, rows=None, *, request_timeout=10):
        payload = {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if rows:
            payload["reply_markup"] = {"inline_keyboard": rows}
        return self.call("sendMessage", request_timeout=request_timeout, **payload)

    def edit(self, chat_id, message_id, text, rows=None, *, request_timeout=10):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text,
                   "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
                   "reply_markup": {"inline_keyboard": rows or []}}
        return self.call("editMessageText", request_timeout=request_timeout, **payload)

    def answer_callback(self, callback_id):
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id)
        except TelegramError as error:
            if error.code != 400:
                raise
