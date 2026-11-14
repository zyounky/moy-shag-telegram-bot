import logging
import re
from datetime import UTC, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .advice import STAGES, catalog_context, compose_advice, infer_profile
from .reminders import resolve_local_time
from .progress import DraftReply


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
CONTROLS = [
    [{"text": "Мой шаг", "callback_data": "action:show"},
     {"text": "Напоминания", "callback_data": "reminders"}],
    [{"text": "Контекст бизнеса", "callback_data": "profile:show"}],
]


def button(label: str, callback: str) -> dict:
    return {"text": label, "callback_data": callback}


def link(label: str, url: str) -> dict:
    return {"text": label, "url": url}


def h(text) -> str:
    return escape(str(text), quote=False)


def _format_local(timestamp: datetime, timezone: str) -> str:
    date = timestamp.astimezone(ZoneInfo(timezone))
    return f"{date:%d.%m.%Y в %H:%M} ({TIME_ZONES.get(timezone, timezone)})"


def _demo_notice(catalog) -> str:
    return "\n\n<i>Демонстрационный каталог. Источники учебные.</i>" if catalog.is_demo else ""


class Assistant:
    def __init__(self, api, catalog, conversations, reminders, chooser=None, now=None, generator=None):
        self.api = api
        self.catalog = catalog
        self.conversations = conversations
        self.reminders = reminders
        self.chooser = chooser
        self.generator = generator
        self.now = now or (lambda: datetime.now(UTC))

    def send(self, chat: int, message: str, rows=None):
        return self.api.send(chat, message, rows)

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
            self.message(chat["id"], message["text"].strip())

    def message(self, chat: int, text: str):
        state = self.conversations.get(chat)
        verb = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
        if verb == "/start" or verb == "/help":
            state.pending.clear()
            state.topic = ""
            self.conversations.save(chat, state)
            self.send(chat, "<b>Помощник «Альфа-Курса»</b>\n"
                            "Опишите задачу своими словами. Я найду подходящий материал "
                            "и помогу выбрать, что делать дальше.\n\n"
                            "Например: «Как составить бизнес-план?» или «Что такое NPS?»"
                            + _demo_notice(self.catalog), CONTROLS)
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
        elif re.match(r"^напомни(?:те)?\b", text, re.I) and not state.pending:
            title = re.sub(r"^напомни(?:те)?\b[ ,:]*", "", text, flags=re.I).strip()
            self.begin_reminder(chat, state, title[:150] or None)
        elif state.pending.get("kind") == "reminder":
            self.reminder_text(chat, state, text)
        elif state.pending.get("kind") in ("clarify", "obstacle"):
            obstacle = text if state.pending["kind"] == "obstacle" else ""
            query = state.pending.pop("query")
            state.pending.clear()
            infer_profile(text, state.profile)
            question = query if obstacle else f"{query}. {text}"[:600]
            self.answer(chat, state, question, clarified=True, obstacle=obstacle)
        else:
            followup = bool(re.match(
                r"^(?:а\s+(?:если|как|что|почему)|как\s+это|что\s+это|объясни\s+подробнее|"
                r"подробнее\b|не\s+получилось|у\s+меня\s+нет|нет\s+данных)", text, re.I))
            question = f"{state.topic}\nУточнение: {text}"[:750] if state.topic and followup else text[:600]
            self.answer(chat, state, question)

    def show_profile(self, chat, state):
        context = catalog_context(state.profile)
        bits = [context["sector"], context["stage"], context["goal"]]
        profile = " · ".join(v for v in bits if v) or "Пока пусто"
        self.send(chat, "<b>Контекст бизнеса</b>\n" + h(profile) + "\n\n"
                        "Выберите стадию или опишите ситуацию в следующем вопросе.",
                  [[b] for b in STAGE_BUTTONS[:3]] + [[button("Сбросить профиль", "profile:reset")]])

    def _clarify(self, chat, state, question):
        state.pending = {"kind": "clarify", "query": question}
        self.conversations.save(chat, state)
        if state.profile.get("stage"):
            self.send(chat, "Какой результат нужен сейчас? Например, проверить спрос, "
                            "привлечь клиентов или разобраться в учёте.")
        else:
            self.send(chat, "Вы уже ведёте бизнес или готовитесь запустить его? "
                            "Можно выбрать стадию или уточнить задачу сообщением.",
                      [[b] for b in STAGE_BUTTONS])

    def answer(self, chat, state, question: str, clarified: bool = False, obstacle: str = ""):
        if not question:
            return self._clarify(chat, state, "")
        infer_profile(question, state.profile)
        if "\nУточнение:" not in question:
            state.topic = question
        context = catalog_context(state.profile)
        result = self.catalog.search(question, context)
        if obstacle:
            refined = self.catalog.search(f"{question}. {obstacle}"[:750], context)
            if refined.evidence:
                result = refined
        if result.needs_clarification and not clarified:
            return self._clarify(chat, state, question)
        state.pending.clear()
        if not result.evidence:
            self.conversations.save(chat, state)
            sections = ["В доступных материалах «Альфа-Курса» я не нашёл подтверждённого ответа. "
                        "Попробуйте уточнить задачу, например указать сферу бизнеса или что уже пробовали."]
            rows = list(CONTROLS)
            self.add_recommendations(result, sections, rows)
            self.send(chat, "\n\n".join(sections) + _demo_notice(self.catalog), rows)
            return

        evidence = result.evidence[0]
        if self.chooser:
            evidence = self.chooser.choose(question, context, result.evidence)
        advice = compose_advice(question, evidence, state.profile, obstacle)
        reply = DraftReply(self.api, chat) if self.generator and callable(getattr(self.api, "edit", None)) else None
        finish = reply.finish if reply else lambda text, rows=None: self.send(chat, text, rows)
        if self.generator:
            generated = self.generator.generate(question, state.profile, result.evidence,
                                                obstacle=obstacle, is_demo=self.catalog.is_demo, chat_id=chat,
                                                on_preview=reply.update if reply else None)
            LOG.info("Ответ LLM: %s; время %s сек.; первый текст %s сек.; кеш %s",
                     self.generator.last_status, self.generator.last_metrics.get("total_seconds"),
                     self.generator.last_metrics.get("first_answer_seconds"),
                     self.generator.last_metrics.get("cache_hit"))
            if generated:
                evidence, advice = generated
            else:
                status = self.generator.last_status
                if status == "insufficient":
                    text = ("В найденных материалах недостаточно сведений для уверенного ответа. "
                            + (self.generator.last_clarification or "Уточните, какой результат хотите получить."))
                elif status in {"disabled", "expired", "budget"}:
                    text = "Сейчас доступна подборка материалов; консультация модели для этого чата не активна."
                else:
                    text = "Не удалось подготовить ответ. Попробуйте ещё раз; подходящие материалы доступны ниже."
                sections = [text]
                rows = []
                for item in result.evidence[:2]:
                    sections.append(f"<b>{h(item.title)}</b>")
                    if item.url:
                        rows.append([link("Открыть материал", item.url)])
                rows.extend(CONTROLS)
                self.conversations.save(chat, state)
                return finish("\n\n".join(sections) + _demo_notice(self.catalog), rows)
        action = self.conversations.new_action(advice.action, advice.passage, advice.url)
        action["source_title"] = advice.title
        action["subject"] = question
        action["completion"] = advice.completion
        state.proposal = action
        self.conversations.save(chat, state)

        sections = []
        if advice.context:
            sections.append(f"<i>Учёл контекст: {h(advice.context)}.</i>")
        source = h(advice.title)
        if evidence.heading:
            source += f" · {h(evidence.heading)}"
        if obstacle:
            sections.append(f"Уточнение к шагу: {h(obstacle[:130])}")
        if advice.answer:
            sections.append(f"<b>По вашей задаче</b>\n{h(advice.answer)}")
            sections.append("<b>Источники</b>\n" + "\n".join(h(title) for title, _ in advice.sources))
        else:
            sections.append(f"<b>Что нашёл в «Альфа-Курсе»</b>\n<i>{source}</i>\n{h(advice.passage)}")
        sections.append(f"<b>Что сделать дальше</b>\n{h(advice.action)}")
        if advice.completion:
            sections.append(f"<b>Как проверить результат</b>\n{h(advice.completion)}")
        if advice.regulated:
            sections.append("Источник может описывать правила за прошлый период.")
        rows = [[link("Открыть источник", advice.url)]] if advice.url else []
        for title, url in advice.sources:
            if url and url != advice.url and all(row[0].get("url") != url for row in rows):
                rows.append([link("Источник: " + title[:35], url)])
        rows.append([button("Взять шаг в работу", f"action:take:{action['id']}")])
        self.add_recommendations(result, sections, rows, advice.url, advice.title)
        rows.append(CONTROLS[0])
        finish("\n\n".join(sections) + _demo_notice(self.catalog), rows)

    def add_recommendations(self, result, sections, rows, source_url=None, source_title=None):
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
            label = item.price_display or "Материал"
            summary = (item.description or item.reason or "").strip()
            if len(summary) > 200:
                summary = summary[:198].rsplit(" ", 1)[0] + "…"
            benefit = item.reason.strip()
            sections.append(f"<b>{h(label)} · {h(item.title)}</b>\n{h(summary)}"
                            + (f"\n<i>{h(benefit)}</i>" if summary and benefit and benefit != summary else ""))
            if item.url:
                rows.append([link("Подробнее о курсе" if item.kind == "course" else "Открыть материал", item.url)])

    def show_action(self, chat, state):
        action = state.action or state.proposal
        if not action:
            return self.send(chat, "Задайте вопрос, чтобы выбрать первый шаг.", CONTROLS)
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
        progress = f"\n\n<b>Как проверить результат</b>\n{h(completion)}" if completion else ""
        self.send(chat, f"<b>Мой шаг · {h(status)}</b>\n{h(action['title'])}{progress}\n\n"
                        f"Источник: {h(action['source_title'])}", rows)

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
            rows = [[link("Открыть источник", action["source"])]] if action["source"] else CONTROLS
            return self.send(chat, "Шаг выполнен. Можно задать новый вопрос или открыть исходный материал.",
                             rows)
        if intent == "blocked" and action["status"] in ("active", "blocked"):
            action["status"] = "blocked"
            state.pending = {"kind": "obstacle", "query": action["subject"]}
            self.conversations.save(chat, state)
            return self.send(chat, "Что помешало выполнить шаг? Напишите одним сообщением, "
                                   "и я попробую найти материал под это препятствие.")
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
                  [[button("Обучение", "rem:type:study"), button("Бизнес-событие", "rem:type:business")]])

    def ask_when(self, chat, state):
        pending = state.pending
        pending["step"] = "when"
        timezone = pending.get("timezone", state.profile.get("timezone", "Europe/Moscow"))
        pending["timezone"] = timezone
        self.conversations.save(chat, state)
        zone_name = TIME_ZONES.get(timezone, timezone)
        example = (self.now().astimezone(ZoneInfo(timezone)) + timedelta(days=1)).replace(hour=19, minute=0)
        self.send(chat, f"Когда напомнить? Сейчас выбран часовой пояс <b>{h(zone_name)}</b>. "
                        f"Введите дату и время, например <code>{example:%d.%m.%Y %H:%M}</code>.",
                  [[button("Через минуту", "rem:in:1"), button("Завтра в 19:00", "rem:in:tomorrow")],
                   [button("Часовой пояс", "rem:tz"), button("Отмена", "rem:cancel")]])

    def ask_frequency(self, chat, state, timestamp):
        pending = state.pending
        if timestamp <= self.now():
            return self.send(chat, "Это время уже прошло. Укажите будущее время.")
        pending.update(step="frequency", scheduled_for=timestamp.isoformat())
        self.conversations.save(chat, state)
        self.send(chat, "Как часто напоминать?",
                  [[button(label.capitalize(), f"rem:freq:{key}")] for key, label in FREQUENCIES.items()]
                  + [[button("Поменять время", "rem:when"), button("Отмена", "rem:cancel")]])

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
                return self.send(chat, "Проверьте дату и время. Формат: ДД.ММ.ГГГГ ЧЧ:ММ.")
            return self.ask_frequency(chat, state, timestamp)
        return self.send(chat, "Выберите вариант кнопкой или введите /cancel.")

    def show_reminders(self, chat):
        entries = self.reminders.list_active(chat)
        if not entries:
            return self.send(chat, "Активных напоминаний нет.", [[button("Добавить", "rem:new")]])
        lines = ["<b>Мои напоминания</b>"]
        rows = []
        for item in entries[:10]:
            lines.append(f"{h(item.title)}\n{h(_format_local(item.next_run, item.timezone))} · "
                         f"{FREQUENCIES[item.frequency]}")
            rows.append([button(f"Изменить №{item.id}", f"manage:edit:{item.id}"),
                         button(f"Отключить №{item.id}", f"manage:off:{item.id}")])
        rows.append([button("Добавить", "rem:new")])
        self.send(chat, "\n\n".join(lines), rows)

    def reminder_callback(self, chat, state, payload):
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
            return self.send(chat, "Введите название. Например: "
                             + ("«Дочитать материал»." if arg == "study" else "«Отправить отчёт»."))
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
            date = _format_local(datetime.fromisoformat(pending["scheduled_for"]), pending["timezone"])
            return self.send(chat, f"<b>Подтвердите напоминание</b>\n{h(pending['title'])}\n"
                                   f"{h(date)} · {FREQUENCIES[arg]}",
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
            return self.send(chat, f"Напоминание №{entry.id} создано. "
                                   f"Ближайшая отправка — {h(_format_local(entry.next_run, entry.timezone))}.",
                             [[button("Мои напоминания", "reminders")]])

    def manage_reminder(self, chat, payload):
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
            return self.send(chat, f"Новая частота для «{h(item.title)}»?",
                             [[button(label.capitalize(), f"manage:set:{reminder_id}:{freq}")]
                              for freq, label in FREQUENCIES.items()])
        if intent == "set" and frequency in FREQUENCIES:
            item = self.reminders.set_frequency(chat, reminder_id, frequency, now=self.now())
            if item:
                return self.send(chat, f"Частота обновлена: {FREQUENCIES[frequency]}. "
                                       f"Ближайшая отправка — {h(_format_local(item.next_run, item.timezone))}.",
                                 [[button("Мои напоминания", "reminders")]])
            return self.send(chat, "Напоминание уже завершено или отключено.")

    def callback(self, chat, data):
        state = self.conversations.get(chat)
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
            state.profile.clear()
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
            action = state.action
            rows = []
            if item.action_url:
                rows.append([link(item.action_label or "Открыть материал", item.action_url)])
            if item.id in action.get("reminder_ids", []) and action.get("status") in ("active", "blocked"):
                rows.append([button("Сделано", f"action:done:{action['id']}"),
                             button("Есть препятствие", f"action:blocked:{action['id']}")])
            rows.append([button("Мои напоминания", "reminders")])
            try:
                self.send(item.chat_id, f"<b>Напоминание</b>\n{h(item.title)}", rows)
            except Exception as error:
                LOG.warning("Напоминание %s: ошибка доставки %s", item.id, type(error).__name__)
                if getattr(error, "code", None) == 403:
                    self.reminders.disable(item.chat_id, item.id)
                else:
                    self.reminders.retry(item, at=self.now())
            else:
                self.reminders.acknowledge(item, at=self.now())
