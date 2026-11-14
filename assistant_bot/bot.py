import logging
import re
from datetime import UTC, datetime, timedelta
from functools import partial
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .advice import STAGES, catalog_context, compose_advice, infer_profile
from .query_language import russian_search_query
from .reminders import resolve_local_time
from .ui import SCREENS, text as ui_text


LOG = logging.getLogger(__name__)
FREQUENCIES = {"once": "один раз", "daily": "ежедневно", "weekly": "еженедельно"}
TIME_ZONES = {
    "Europe/Moscow": "Москва",
    "Asia/Yekaterinburg": "Екатеринбург",
    "Asia/Novosibirsk": "Новосибирск",
    "Asia/Vladivostok": "Владивосток",
}
STAGE_BUTTONS = [
    {"text": "Готовлюсь к запуску", "callback_data": "stage:launch"},
    {"text": "Уже работаю", "callback_data": "stage:operate"},
    {"text": "Развиваю бизнес", "callback_data": "stage:grow"},
    {"text": "Пропустить", "callback_data": "stage:skip"},
]
LANGUAGE_BUTTONS = [[{"text": "Русский", "callback_data": "lang:ru"},
                     {"text": "English", "callback_data": "lang:en"}]]
MENU = [
    [{"text": "Задать вопрос", "callback_data": "menu:ask"}],
    [{"text": "Мой шаг", "callback_data": "action:show"},
     {"text": "Напоминания", "callback_data": "reminders"}],
    [{"text": "Помощь", "callback_data": "menu:help"},
     {"text": "Что умеет бот", "callback_data": "menu:about"}],
    [{"text": "Контекст бизнеса", "callback_data": "profile:show"},
     {"text": "Язык", "callback_data": "lang:show"}],
]
CONTROLS = [[{"text": "В меню", "callback_data": "menu:home"}]]


def button(label: str, callback: str) -> dict:
    return {"text": label, "callback_data": callback}


def link(label: str, url: str) -> dict:
    return {"text": label, "url": url}


def h(text) -> str:
    return escape(str(text), quote=False)


def _format_local(timestamp: datetime, timezone: str, language: str) -> str:
    date = timestamp.astimezone(ZoneInfo(timezone))
    zone = ui_text(language, TIME_ZONES.get(timezone, timezone))
    return f"{date:%d.%m.%Y %H:%M} ({zone})"


def _demo_notice(catalog, t) -> str:
    return f"\n\n<i>{t('Демонстрационный каталог. Источники учебные.')}</i>" if catalog.is_demo else ""


class Assistant:
    def __init__(self, api, catalog, conversations, reminders, chooser=None, now=None):
        self.api = api
        self.catalog = catalog
        self.conversations = conversations
        self.reminders = reminders
        self.chooser = chooser
        self.now = now or (lambda: datetime.now(UTC))

    def send(self, chat: int, message: str, rows=None, *, language=None):
        if language is None:
            language = self.conversations.get(chat).language
        return self.api.send(chat, ui_text(language, message), self.localized_rows(rows, language))

    @staticmethod
    def localized_rows(rows, language):
        if not rows or language != "en":
            return rows
        return [[{**item, "text": ui_text(language, item["text"])} for item in row] for row in rows]

    def show_language(self, chat):
        self.send(chat, "<b>Выберите язык · Choose your language</b>", LANGUAGE_BUTTONS)

    def show_home(self, chat, state):
        screen = SCREENS.get(state.language, SCREENS["ru"])
        t = partial(ui_text, state.language)
        current = state.action
        detail = screen["example"]
        if current.get("status") in ("active", "blocked") and current.get("title"):
            detail = screen["current_step"].format(step=h(current["title"]))
        if state.language == "en":
            detail += "\n\n" + t("Поддерживаются типовые вопросы на английском. Материалы курса и советы могут быть на русском.")
        self.send(chat, f"{screen['welcome']}\n\n{detail}" + _demo_notice(self.catalog, t), MENU)

    def show_screen(self, chat, state, screen):
        rows = CONTROLS
        if screen == "help":
            rows = [[button("Что умеет бот", "menu:about"), button("Язык", "lang:show")]] + rows
        self.send(chat, SCREENS.get(state.language, SCREENS["ru"])[screen], rows)

    def update(self, update: dict):
        callback = update.get("callback_query")
        if callback:
            message = callback.get("message", {})
            chat = message.get("chat", {})
            self.api.answer_callback(callback["id"])
            if chat.get("type") == "private":
                self.callback(chat["id"], callback.get("data", ""))
            return
        message = update.get("message", {})
        chat = message.get("chat", {})
        if chat.get("type") == "private" and message.get("text"):
            self.message(chat["id"], message["text"].strip(), require_language=True)

    def message(self, chat: int, text: str, *, require_language=False):
        state = self.conversations.get(chat)
        verb = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
        if not state.language and not any((state.profile, state.action, state.proposal)):
            choosing = state.pending.get("kind") == "language"
            needs_choice = verb in ("/start", "/menu") or (
                (choosing or require_language) and text and not verb.startswith("/")
            )
            if needs_choice and (choosing or not state.pending):
                if not choosing:
                    state.pending = {"kind": "language"}
                if text and not verb.startswith("/"):
                    state.pending["question"] = text[:600]
                self.conversations.save(chat, state)
                return self.show_language(chat)
        if verb in ("/start", "/menu"):
            self.show_home(chat, state)
        elif verb in ("/help", "/about"):
            self.show_screen(chat, state, verb[1:])
        elif verb == "/language":
            self.show_language(chat)
        elif verb == "/cancel":
            state.pending.clear()
            self.conversations.save(chat, state)
            self.send(chat, "Действие отменено.", CONTROLS)
        elif verb == "/reminders":
            self.show_reminders(chat)
        elif verb == "/step":
            self.show_action(chat, state)
        elif verb == "/profile":
            self.show_profile(chat, state)
        elif verb == "/remind":
            self.begin_reminder(chat, state)
        elif verb.startswith("/"):
            self.send(chat, "Я не распознал команду. Доступные команды — в /help.",
                      [[button("Помощь", "menu:help")]])
        elif (remind := re.match(r"^(?:напомни(?:те)?|remind me(?: to| about)?)\b[ ,:]*", text, re.I)) and not state.pending:
            title = text[remind.end():].strip()
            self.begin_reminder(chat, state, title[:150] or None)
        elif state.pending.get("kind") == "reminder":
            self.reminder_text(chat, state, text)
        elif state.pending.get("kind") in ("clarify", "obstacle"):
            obstacle = text if state.pending["kind"] == "obstacle" else ""
            query = state.pending["query"]
            state.pending.clear()
            infer_profile(russian_search_query(text), state.profile)
            question = query if obstacle else f"{query}\n{text}"[:600]
            self.answer(chat, state, question, clarified=True, obstacle=obstacle)
        else:
            self.answer(chat, state, text[:600])

    def show_profile(self, chat, state):
        t = partial(ui_text, state.language)
        context = catalog_context(state.profile)
        bits = [context["sector"], context["stage"], context["goal"]]
        profile = " · ".join(t(v) for v in bits if v) or t("Пока пусто")
        self.send(chat, f"<b>{t('Контекст бизнеса')}</b>\n{h(profile)}\n\n"
                        + t("Заполните профиль или опишите ситуацию в следующем вопросе."),
                  [[b] for b in STAGE_BUTTONS[:3]] + [[button("Сбросить профиль", "profile:reset")]] + CONTROLS)

    def _clarify(self, chat, state, question):
        state.pending = {"kind": "clarify", "query": question}
        self.conversations.save(chat, state)
        if state.profile.get("stage"):
            self.send(chat, "Какой результат нужен сейчас? Например, проверить спрос, "
                            "привлечь клиентов или разобраться в учёте.", CONTROLS)
        else:
            self.send(chat, "Укажите стадию бизнеса или уточните задачу сообщением.",
                      [[b] for b in STAGE_BUTTONS] + CONTROLS)

    def answer(self, chat, state, question: str, clarified: bool = False, obstacle: str = ""):
        if not question:
            return self._clarify(chat, state, "")
        t = partial(ui_text, state.language)
        # Process follow-ups line by line to preserve both Russian and English inputs.
        search_query = "\n".join(russian_search_query(line) for line in question.splitlines())
        search_obstacle = russian_search_query(obstacle)
        infer_profile(search_query, state.profile)
        context = catalog_context(state.profile)
        result = self.catalog.search(search_query, context)
        if obstacle:
            refined = self.catalog.search(f"{search_query}. {search_obstacle}"[:750], context)
            if refined.evidence:
                result = refined
        if result.needs_clarification and not clarified:
            return self._clarify(chat, state, question)
        state.pending.clear()
        if not result.evidence:
            self.conversations.save(chat, state)
            sections = [t("В доступных материалах «Альфа-Курса» я не нашёл подтверждённого ответа. "
                          "Попробуйте уточнить задачу, например указать сферу бизнеса или что уже пробовали.")]
            rows = list(CONTROLS)
            self.add_recommendations(result, sections, rows, t)
            self.send(chat, "\n\n".join(sections) + _demo_notice(self.catalog, t), rows)
            return

        evidence = result.evidence[0]
        if self.chooser:
            evidence = self.chooser.choose(search_query, context, result.evidence)
        advice = compose_advice(search_query, evidence, state.profile, search_obstacle)
        action = self.conversations.new_action(advice.action, advice.passage, advice.url)
        action["source_title"] = advice.title
        action["subject"] = question
        action["completion"] = advice.completion
        state.proposal = action
        self.conversations.save(chat, state)

        sections = []
        if advice.context:
            display_context = " · ".join(t(value) for value in advice.context.split(" · "))
            sections.append(f"<i>{t('Учёл контекст')}: {h(display_context)}.</i>")
        source = h(advice.title)
        if evidence.heading:
            source += f" · {h(evidence.heading)}"
        if obstacle:
            sections.append(f"{t('Уточнение к шагу')}: {h(obstacle[:130])}")
        sections.append(f"<b>{t('Что нашёл в «Альфа-Курсе»')}</b>\n<i>{source}</i>\n{h(advice.passage)}")
        sections.append(f"<b>{t('Что сделать дальше')}</b>\n{h(advice.action)}")
        if advice.completion:
            sections.append(f"<b>{t('Как проверить результат')}</b>\n{h(advice.completion)}")
        if advice.regulated:
            sections.append(t("Источник может описывать правила за прошлый период."))
        rows = [[link("Открыть источник", advice.url)]] if advice.url else []
        rows.append([button("Взять шаг в работу", f"action:take:{action['id']}")])
        self.add_recommendations(result, sections, rows, t, advice.url, advice.title)
        rows.extend(CONTROLS)
        self.send(chat, "\n\n".join(sections) + _demo_notice(self.catalog, t), rows)

    def add_recommendations(self, result, sections, rows, t, source_url=None, source_title=None):
        seen = {source_url} if source_url else set()
        cards = []
        for item in result.recommendations:
            if item.title == source_title or (item.url and item.url in seen):
                continue
            if item.url:
                seen.add(item.url)
            cards.append(item)
        paid = next((item for item in cards if item.is_paid), None)
        selected = [item for item in cards if not item.is_paid][:1 if paid else 2]
        if paid:
            selected.append(paid)
        for item in selected:
            label = t(item.price_display or "Материал")
            summary = (item.description or t(item.reason) or "").strip()
            if len(summary) > 200:
                summary = summary[:198].rsplit(" ", 1)[0] + "…"
            benefit = t(item.reason.strip())
            sections.append(f"<b>{h(label)} · {h(item.title)}</b>\n{h(summary)}"
                            + (f"\n<i>{h(benefit)}</i>" if summary and benefit and benefit != summary else ""))
            if item.url:
                rows.append([link("Подробнее о курсе" if item.kind == "course" else "Открыть материал", item.url)])

    def show_action(self, chat, state):
        action = state.action or state.proposal
        if not action:
            return self.send(chat, "Задайте вопрос, чтобы выбрать первый шаг.",
                             [[button("Задать вопрос", "menu:ask")]] + CONTROLS)
        t = partial(ui_text, state.language)
        action_id = action["id"]
        status = {"suggested": "Предложен", "active": "В работе", "done": "Выполнен",
                  "blocked": "Нужна помощь"}.get(action["status"], "")
        rows = [[link("Открыть источник", action["source"])]] if action["source"] else []
        if action["status"] == "suggested":
            rows.append([button("Взять шаг в работу", f"action:take:{action_id}")])
        if action["status"] in ("active", "blocked"):
            rows += [[button("Сделано", f"action:done:{action_id}"),
                      button("Есть препятствие", f"action:blocked:{action_id}")],
                     [button("Напомнить об этом шаге", f"action:remind:{action_id}")]]
        completion = action.get("completion")
        progress = f"\n\n<b>{t('Как проверить результат')}</b>\n{h(completion)}" if completion else ""
        self.send(chat, f"<b>{t('Мой шаг')} · {h(t(status))}</b>\n{h(action['title'])}{progress}\n\n"
                        f"{t('Источник')}: {h(action['source_title'])}", rows + CONTROLS)

    def action_callback(self, chat, state, payload: str):
        if payload == "show":
            return self.show_action(chat, state)
        intent, _, action_id = payload.partition(":")
        action = state.action
        proposal = state.proposal
        if intent in ("take", "replace") and proposal.get("id") == action_id:
            if intent == "take" and action.get("status") in ("active", "blocked"):
                return self.send(chat, "У вас уже есть шаг в работе. Заменить его новым?",
                                 [[button("Заменить шаг", f"action:replace:{action_id}"),
                                   button("Оставить текущий", "action:show")]])
            self.disable_action_reminders(chat, action)
            proposal["status"] = "active"
            state.action, state.proposal = proposal, {}
            self.conversations.save(chat, state)
            return self.show_action(chat, state)
        if not action or action["id"] != action_id:
            return self.send(chat, "Это действие относится к предыдущему шагу.", CONTROLS)
        if intent == "remind" and action["status"] in ("active", "blocked"):
            return self.begin_reminder(chat, state, action["title"], action)
        if intent == "done" and action["status"] in ("active", "blocked"):
            action["status"] = "done"
            self.disable_action_reminders(chat, action)
            self.conversations.save(chat, state)
            rows = [[link("Открыть источник", action["source"])]] if action["source"] else []
            return self.send(chat, "Шаг выполнен. Можно задать новый вопрос или открыть исходный материал.",
                             rows + [[button("Задать вопрос", "menu:ask")]] + CONTROLS)
        if intent == "blocked" and action["status"] in ("active", "blocked"):
            action["status"] = "blocked"
            state.pending = {"kind": "obstacle", "query": action["subject"]}
            self.conversations.save(chat, state)
            return self.send(chat, "Что помешало выполнить шаг? Напишите одним сообщением, "
                                   "и я попробую найти материал под это препятствие.", CONTROLS)
        return self.show_action(chat, state)

    def disable_action_reminders(self, chat, action):
        for reminder_id in action.get("reminder_ids", []):
            self.reminders.disable(chat, reminder_id)

    def begin_reminder(self, chat, state, title=None, action=None):
        state.pending = {"kind": "reminder", "step": "type"}
        if title:
            state.pending["title"] = title
        if action:
            state.pending.update(step="when", action_label="Открыть источник", action_url=action["source"],
                                 action_id=action["id"])
            return self.ask_when(chat, state)
        self.conversations.save(chat, state)
        self.send(chat, "О чём напомнить?",
                  [[button("Обучение", "rem:type:study"), button("Бизнес-событие", "rem:type:business")],
                   [button("Отмена", "rem:cancel")]])

    def ask_when(self, chat, state):
        pending = state.pending
        pending["step"] = "when"
        timezone = pending.get("timezone", state.profile.get("timezone", "Europe/Moscow"))
        pending["timezone"] = timezone
        self.conversations.save(chat, state)
        t = partial(ui_text, state.language)
        zone_name = t(TIME_ZONES.get(timezone, timezone))
        example = (self.now().astimezone(ZoneInfo(timezone)) + timedelta(days=1)).replace(hour=19, minute=0)
        self.send(chat, t("Когда напомнить? Часовой пояс — <b>{zone}</b>. "
                          "Введите дату и время, например <code>{example}</code>.",
                          zone=h(zone_name), example=f"{example:%d.%m.%Y %H:%M}"),
                  [[button("Через минуту", "rem:in:1"), button("Завтра в 19:00", "rem:in:tomorrow")],
                   [button("Часовой пояс", "rem:tz"), button("Отмена", "rem:cancel")]])

    def ask_frequency(self, chat, state, timestamp):
        pending = state.pending
        if timestamp <= self.now():
            return self.send(chat, "Это время уже прошло. Укажите будущее время.")
        pending.update(step="frequency", scheduled_for=timestamp.isoformat())
        self.conversations.save(chat, state)
        self.send(chat, "Как часто напоминать?",
                  self.frequency_buttons(state.language, "rem:freq")
                  + [[button("Поменять время", "rem:when"), button("Отмена", "rem:cancel")]])

    @staticmethod
    def frequency_buttons(language, prefix):
        return [[button(ui_text(language, label).capitalize(), f"{prefix}:{key}")]
                for key, label in FREQUENCIES.items()]

    def reminder_text(self, chat, state, text):
        pending = state.pending
        if pending["step"] == "title":
            if not text:
                return self.send(chat, "Введите короткое название.")
            pending["title"] = text[:150]
            return self.ask_when(chat, state)
        if pending["step"] == "when":
            try:
                local_time = datetime.strptime(text, "%d.%m.%Y %H:%M")
                timestamp = resolve_local_time(local_time, pending["timezone"])
            except (ValueError, ZoneInfoNotFoundError):
                return self.send(chat, "Проверьте дату и время. Формат ДД.ММ.ГГГГ ЧЧ:ММ.")
            return self.ask_frequency(chat, state, timestamp)
        return self.send(chat, "Выберите вариант кнопкой или введите /cancel.")

    def show_reminders(self, chat):
        language = self.conversations.get(chat).language
        t = partial(ui_text, language)
        entries = self.reminders.list_active(chat)
        add = [[button("Добавить напоминание", "rem:new")]] + CONTROLS
        if not entries:
            return self.send(chat, "Активных напоминаний нет.", add)
        lines = [f"<b>{t('Мои напоминания')}</b>"]
        rows = []
        for item in entries[:10]:
            lines.append(f"{h(item.title)}\n{h(_format_local(item.next_run, item.timezone, language))} · "
                         f"{t(FREQUENCIES[item.frequency])}")
            rows.append([button(t("Изменить №{id}", id=item.id), f"manage:edit:{item.id}"),
                         button(t("Отключить №{id}", id=item.id), f"manage:off:{item.id}")])
        self.send(chat, "\n\n".join(lines), rows + add)

    def reminder_callback(self, chat, state, payload):
        t = partial(ui_text, state.language)
        key, _, arg = payload.partition(":")
        if key == "new":
            return self.begin_reminder(chat, state)
        pending = state.pending
        if pending.get("kind") != "reminder":
            return self.send(chat, "Эта настройка уже закрыта. Откройте /remind, чтобы создать напоминание.")
        step = pending.get("step")
        if key == "cancel":
            state.pending.clear()
            self.conversations.save(chat, state)
            return self.send(chat, "Создание отменено.", CONTROLS)
        if key == "type" and arg in ("study", "business") and step == "type":
            if pending.get("title"):
                return self.ask_when(chat, state)
            pending["step"] = "title"
            self.conversations.save(chat, state)
            example = ("Введите название. Например, «Дочитать материал»." if arg == "study"
                       else "Введите название. Например, «Отправить отчёт».")
            return self.send(chat, example, [[button("Отмена", "rem:cancel")]])
        if key == "tz" and step == "when":
            return self.send(chat, "Выберите часовой пояс.",
                             [[button(name, f"rem:zone:{tz}")] for tz, name in TIME_ZONES.items()])
        if key == "zone" and arg in TIME_ZONES and step == "when":
            pending["timezone"] = state.profile["timezone"] = arg
            return self.ask_when(chat, state)
        if key == "in" and arg in ("1", "tomorrow") and step == "when":
            now = self.now()
            local = now.astimezone(ZoneInfo(pending["timezone"]))
            scheduled = now + timedelta(minutes=1)
            if arg == "tomorrow":
                scheduled = (local + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
            return self.ask_frequency(chat, state, scheduled)
        if key == "when" and step in ("frequency", "confirm"):
            return self.ask_when(chat, state)
        if key == "freq" and arg in FREQUENCIES and step == "frequency":
            pending.update(step="confirm", frequency=arg)
            self.conversations.save(chat, state)
            date = _format_local(datetime.fromisoformat(pending["scheduled_for"]), pending["timezone"], state.language)
            return self.send(chat, f"<b>{t('Подтвердите напоминание')}</b>\n{h(pending['title'])}\n"
                                   f"{h(date)} · {t(FREQUENCIES[arg])}",
                             [[button("Создать", "rem:confirm"), button("Отмена", "rem:cancel")],
                              [button("Поменять время", "rem:when")]])
        if key == "confirm" and step == "confirm":
            try:
                entry = self.reminders.create(
                    chat, pending["title"], datetime.fromisoformat(pending["scheduled_for"]),
                    pending["timezone"], pending["frequency"], action_label=pending.get("action_label"),
                    action_url=pending.get("action_url"), now=self.now(),
                )
            except ValueError:
                self.send(chat, "Это время уже прошло. Укажите новое.")
                return self.ask_when(chat, state)
            if pending.get("action_id") == state.action.get("id") and state.action:
                state.action.setdefault("reminder_ids", []).append(entry.id)
            state.pending.clear()
            self.conversations.save(chat, state)
            date = h(_format_local(entry.next_run, entry.timezone, state.language))
            return self.send(chat, t("Напоминание №{id} создано. Ближайшая отправка — {date}.",
                                     id=entry.id, date=date),
                             [[button("Мои напоминания", "reminders")]] + CONTROLS)

    def manage_reminder(self, chat, payload):
        language = self.conversations.get(chat).language
        t = partial(ui_text, language)
        intent, _, rest = payload.partition(":")
        item_id, _, frequency = rest.partition(":")
        if not item_id.isdecimal():
            return
        reminder_id = int(item_id)
        if intent == "off":
            disabled = self.reminders.disable(chat, reminder_id)
            return self.send(chat, "Напоминание отключено." if disabled else "Напоминание уже отключено.",
                             [[button("Мои напоминания", "reminders")]])
        if intent == "edit":
            item = self.reminders.get(chat, reminder_id)
            if not item or not item.active:
                return self.send(chat, "Напоминание уже завершено или отключено.")
            return self.send(chat, t("Новая частота для «{title}»?", title=h(item.title)),
                             self.frequency_buttons(language, f"manage:set:{reminder_id}") + CONTROLS)
        if intent == "set" and frequency in FREQUENCIES:
            item = self.reminders.set_frequency(chat, reminder_id, frequency, now=self.now())
            if item:
                date = h(_format_local(item.next_run, item.timezone, language))
                return self.send(chat, t("Частота обновлена — {frequency}. Ближайшая отправка — {date}.",
                                         frequency=t(FREQUENCIES[frequency]), date=date),
                                 [[button("Мои напоминания", "reminders")]] + CONTROLS)
            return self.send(chat, "Напоминание уже завершено или отключено.")

    def callback(self, chat, data):
        state = self.conversations.get(chat)
        if data == "lang:show":
            return self.show_language(chat)
        if data in ("lang:ru", "lang:en"):
            state.language = data[-2:]
            question = ""
            if state.pending.get("kind") == "language":
                question = state.pending.get("question", "")
                state.pending.clear()
            self.conversations.save(chat, state)
            if question:
                return self.message(chat, question)
            return self.show_home(chat, state)
        if data == "menu:home":
            return self.message(chat, "/menu")
        if data == "menu:ask":
            state.pending.clear()
            self.conversations.save(chat, state)
            return self.show_screen(chat, state, "ask")
        if data in ("menu:help", "menu:about"):
            return self.show_screen(chat, state, data.partition(":")[2])
        if data == "reminders":
            return self.show_reminders(chat)
        if data == "profile:show":
            return self.show_profile(chat, state)
        if data.startswith("stage:"):
            stage = data.partition(":")[2]
            if stage in STAGES:
                state.profile["stage"] = stage
            if state.pending.get("kind") == "clarify":
                question = state.pending.get("query", "")
                return self.answer(chat, state, question, clarified=True)
            self.conversations.save(chat, state)
            return self.send(chat, "Контекст обновлён. Напишите, с чем помочь.", CONTROLS)
        if data == "profile:reset":
            timezone = state.profile.get("timezone")
            state.profile.clear()
            if timezone:
                state.profile["timezone"] = timezone
            state.pending.clear()
            self.conversations.save(chat, state)
            return self.send(chat, "Контекст очищен.", CONTROLS)
        if data.startswith("action:"):
            return self.action_callback(chat, state, data.removeprefix("action:"))
        if data.startswith("rem:"):
            return self.reminder_callback(chat, state, data.removeprefix("rem:"))
        if data.startswith("manage:"):
            return self.manage_reminder(chat, data.removeprefix("manage:"))

    def deliver_reminders(self):
        for item in self.reminders.due(now=self.now(), limit=10):
            current = self.reminders.get(item.chat_id, item.id)
            if not current or not current.active or current.next_run != item.next_run:
                continue
            state = self.conversations.get(item.chat_id)
            t = partial(ui_text, state.language)
            action = state.action
            rows = []
            if item.action_url:
                rows.append([link(item.action_label or "Открыть материал", item.action_url)])
            if item.id in action.get("reminder_ids", []) and action.get("status") in ("active", "blocked"):
                rows.append([button("Сделано", f"action:done:{action['id']}"),
                             button("Есть препятствие", f"action:blocked:{action['id']}")])
            rows.append([button("Мои напоминания", "reminders")])
            try:
                self.send(item.chat_id, f"<b>{t('Напоминание')}</b>\n{h(item.title)}", rows,
                          language=state.language)
            except Exception as error:
                LOG.warning("Напоминание %s: ошибка доставки %s", item.id, type(error).__name__)
                if getattr(error, "code", None) == 403:
                    self.reminders.disable(item.chat_id, item.id)
                else:
                    self.reminders.retry(item, at=self.now())
            else:
                self.reminders.acknowledge(item, at=self.now())
