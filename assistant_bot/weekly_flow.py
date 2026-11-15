"""Telegram controls for a weekly plan using the existing action and reminder flow."""

import re
from functools import partial
from uuid import uuid4

from .privacy import PrivacyRejected, prepare_plan_note
from .query_language import russian_search_query
from .ui import button, html_text as h, link, text as ui_text
from .weekly_plan import adjustment_for, build_plan, next_material, offerable


OPEN_PLANS = {"active", "paused"}
OUTCOMES = {"success": "Получилось", "partial": "Получилось частично", "failed": "Не получилось"}
STEP_STATUS = {"todo": "Ожидает", "active": "В работе", "review": "Проверьте результат",
               "partial": "Нужна доработка", "failed": "Нужна помощь", "deferred": "Отложено",
               "done": "Выполнено"}
_MINUTES = re.compile(r"(?<!\d)(\d{1,3})\s*(?:мин(?:ут\w*)?|minutes?|mins?)\b", re.I)
_HOURS = re.compile(r"(?<!\d)(\d{1,2})\s*(?:час\w*|hours?)\b", re.I)
_METRICS = {
    "leads": r"заяв(?:ок|ки|ка)|leads?",
    "orders": r"заказ(?:ов|а|ы)?|orders?",
    "visits": r"визит(?:ов|а|ы)?|visits?",
    "expenses": r"расход(?:ы|ов)?|затрат(?:ы|а)?|spend|expenses?",
    "revenue": r"выручк[аиу]|revenue",
    "conversion": r"конверси[яию]|conversion",
}
_NUMBER = r"[0-9]+(?:[ \u00a0][0-9]{3})*(?:[.,][0-9]{1,2})?"


def available_minutes(message):
    if match := _MINUTES.search(message):
        return int(match[1])
    if match := _HOURS.search(message):
        return 60 * int(match[1])
    return None


def aggregate_metrics(note):
    values = {}
    for name, label in _METRICS.items():
        match = re.search(rf"\b(?:{label})\s*[:=—-]?\s*({_NUMBER})(?!\d)"
                          rf"|(?<![\d.,])({_NUMBER})\s+(?:{label})\b", note, re.I)
        if match:
            values[name] = re.sub(r"\s", "", match[1] or match[2]).replace(",", ".")
    return values


class WeeklyFlow:
    def __init__(self, bot):
        self.bot = bot

    def send(self, chat, message, rows=()):
        self.bot.send(chat, message, [*rows, [button("Назад в меню", "menu:home")]])

    def save(self, chat, state):
        self.bot.conversations.save(chat, state)

    @staticmethod
    def seed(state, identifier=None):
        for action in (state.plan_seed, state.proposal, state.action):
            if (action and not action.get("plan_id") and (identifier is None or action.get("id") == identifier)
                    and action.get("plan_eligible", True) and offerable(action.get("subject", ""))):
                return action
        return {}

    @staticmethod
    def linked_step(state):
        action, plan = state.action, state.weekly_plan
        if plan and action.get("plan_id") == plan["id"]:
            return next((step for step in plan["steps"] if step["id"] == action.get("plan_step")), None)
        return None

    @staticmethod
    def current_step(plan):
        return next((step for step in plan["steps"] if step["status"] != "done"), None)

    def open(self, chat, state):
        if state.weekly_plan and state.weekly_plan["status"] in OPEN_PLANS:
            return self.show(chat, state, state.weekly_plan)
        if state.plan_offer:
            return self.show(chat, state, state.plan_offer)
        if state.weekly_plan:
            return self.show(chat, state, state.weekly_plan)
        if seed := self.seed(state):
            return self.send(chat, "Можно составить недельный план по последнему совету.",
                             [[button("Составить план на неделю", f"plan:draft:{seed['id']}")]])
        return self.send(chat, "Задайте вопрос о бизнесе, чтобы составить план на неделю.",
                         [[button("Задать вопрос", "menu:ask")]])

    def draft(self, chat, state, identifier):
        seed = self.seed(state, identifier)
        if not seed:
            return self.send(chat, "Этот совет уже недоступен. Откройте план или задайте новый вопрос.")
        if state.plan_offer.get("seed") == identifier:
            return self.show(chat, state, state.plan_offer)
        subject = seed["subject"]
        known_time = re.search(r"в день|на неделю|в неделю|ежедневно|per day|each day|"
                               r"могу (?:уделить|выделить)|не (?:больше|более)", subject, re.I)
        minutes = available_minutes(subject) if known_time else None
        if minutes:
            return self.build(chat, state, seed, minutes)
        state.pending = {"kind": "weekly_capacity", "seed": identifier}
        self.save(chat, state)
        self.send(chat, "Сколько времени вы готовы выделять в день? Можно ввести свой ответ.",
                  [[button(f"{minutes} минут", f"plan:time:{identifier}:{minutes}") for minutes in (15, 30, 60)],
                   [button("Пока не знаю", f"plan:time:{identifier}:20")]])

    def build(self, chat, state, seed, minutes, *, details=""):
        try:
            subject = prepare_plan_note(seed["subject"], 750)
        except PrivacyRejected:
            state.pending.clear()
            self.save(chat, state)
            return self.send(chat, "Опишите задачу без персональных сведений.",
                             [[button("Задать вопрос", "menu:ask")]])
        original_goal = subject.partition("\nУточнение:")[0][:240]
        subject = f"{subject[:430]}\nУточнение:\n{details[:280]}" if details else subject
        question = "\n".join(russian_search_query(line) for line in subject.splitlines())
        offer = build_plan(self.bot.catalog, question, state.profile, advice=seed,
                           obstacle=seed.get("plan_obstacle", ""), minutes=minutes)
        state.pending.clear()
        if not offer["steps"]:
            prompt = offer.get("clarification_en") if state.language == "en" else offer.get("clarification")
            if not details and prompt:
                state.pending = {"kind": "weekly_details", "seed": seed["id"], "minutes": minutes}
                self.save(chat, state)
                return self.send(chat, prompt)
            self.save(chat, state)
            return self.send(chat, prompt or ui_text(state.language, "Для плана пока мало подходящих материалов."),
                             [[button("Задать вопрос", "menu:ask")]])
        offer.update(id=uuid4().hex[:10], status="offered", seed=seed["id"], question=question,
                     goal=original_goal, used=[])
        for step in offer["steps"]:
            step.update(status="todo", results=[])
        state.plan_offer = offer
        self.save(chat, state)
        return self.show(chat, state, offer)

    def show(self, chat, state, plan, notice=""):
        t = partial(ui_text, state.language)
        identifier = plan["id"]
        status = plan["status"]
        offered = status == "offered"
        rows = []
        titles = {"offered": "Предложение плана на неделю", "paused": "План на паузе",
                  "cancelled": "План отменён", "done": "План завершён", "active": "План на неделю"}
        lines = [f"<b>{t(titles[status])}</b>", f"{t('Цель')}: {h(plan['goal'])}"]
        done = sum(step["status"] == "done" for step in plan["steps"])
        lines.append(t("Выполнено {done} из {total} действий.", done=done, total=len(plan["steps"])))
        if plan.get("limitation"):
            lines.append(h(plan["limitation"]))
        elif plan.get("incomplete"):
            lines.append(t("Материалов хватило на часть плана. Остальное можно уточнить отдельным вопросом."))
        for step in plan["steps"]:
            title = self.step_text(step, "title", state.language)
            detail = (t("Примерно {minutes} мин.", minutes=step["minutes"]) if offered
                      else t(STEP_STATUS[step["status"]]))
            if len(step.get("sessions", ())) > 1:
                detail = (f"{len(step['sessions'])} " + ("sessions" if state.language == "en" else "подхода")
                          + f", ~{step['total_minutes']} " + ("min total" if state.language == "en" else "мин. всего"))
            lines.append(f"{t('День {day}', day=step['day'])} · {h(title)} · {detail}")
        if notice:
            lines.append(t(notice))
        if offered:
            rows += [[button("Начать план", f"plan:activate:{identifier}"),
                      button("Отказаться", f"plan:discard:{identifier}")]]
        elif status == "paused":
            rows.append([button("Продолжить план", f"plan:resume:{identifier}")])
        elif status == "active":
            step = self.current_step(plan)
            if step:
                lines += ["", f"<b>{t('Ближайшее действие')}</b>", self.step_details(step, state.language)]
                if step["status"] in {"active", "review", "partial", "failed"} and self.linked_step(state) is step:
                    rows.append([button("Открыть действие", f"plan:step:{identifier}:{state.action['id']}")])
                else:
                    label = "Продолжить действие" if step["status"] == "deferred" else "Начать действие"
                    rows.append([button(label, f"plan:begin:{identifier}:{step['id']}")])
            rows.append([button("Приостановить план", f"plan:pause:{identifier}")])
        if status in OPEN_PLANS:
            rows.append([button("Отменить план", f"plan:cancel:{identifier}")])
        linked = self.linked_step(state) if not offered else None
        if linked and status in {"active", "done"} and linked["status"] == "done":
            label = "Записать результат" if self.valid_note(state) else "Открыть итог"
            rows.append([button(label, f"plan:step:{identifier}:{state.action['id']}")])
        if not offered and state.plan_offer:
            rows.append([button("Посмотреть новый план", f"plan:offer:{state.plan_offer['id']}")])
        if not offered and (seed := self.seed(state)) and seed["id"] != plan.get("seed"):
            rows.append([button("Составить план на неделю", f"plan:draft:{seed['id']}")])
        self.send(chat, "\n".join(lines), rows)

    @staticmethod
    def step_text(step, key, language):
        return (step.get(f"{key}_en") if language == "en" else None) or step[key]

    def step_details(self, step, language):
        t = partial(ui_text, language)
        detail = [f"<b>{h(self.step_text(step, 'title', language))}</b>",
                  f"{t('Зачем')}: {h(self.step_text(step, 'why', language))}",
                  f"{t('Что сделать')}: {h(self.step_text(step, 'instruction', language))}",
                  f"{t('Как проверить результат')}: {h(self.step_text(step, 'completion', language))}",
                  t("Примерно {minutes} мин.", minutes=step["minutes"])]
        if len(step.get("sessions", ())) > 1:
            detail[-1] = (f"~{step['total_minutes']} " + ("min total, split across days:" if language == "en"
                                                         else "мин. всего, по дням:"))
            for session in step["sessions"]:
                detail.append(f"{t('День {day}', day=session['day'])} · ~{session['minutes']} "
                              + ("min" if language == "en" else "мин.") + f": {h(session['instruction'])}")
            detail.append("Mark the action complete after all sessions and its result check."
                          if language == "en" else "Отметьте действие выполненным после всех подходов и проверки результата.")
        if source := step.get("source"):
            title = source["title"]
            if source.get("heading"):
                title += " · " + source["heading"]
            detail.append(f"{t('Источник')}: {h(title)}")
        return "\n".join(detail)

    def stop_current(self, chat, state, *, keep=False):
        action = state.action
        if action:
            self.bot.disable_action_reminders(chat, action)
            action.pop("reminder_ids", None)
            if not keep:
                state.action = {}
        if state.pending.get("kind", "").startswith("weekly_") or (
                action and state.pending.get("action_id") == action.get("id")):
            state.pending.clear()

    def cancel_for_replacement(self, chat, state):
        if state.weekly_plan.get("status") in OPEN_PLANS:
            state.weekly_plan["status"] = "cancelled"
            self.stop_current(chat, state)

    def begin(self, chat, state, step, adjustment=""):
        plan, source = state.weekly_plan, step["source"]
        title = self.step_text(step, "title", state.language)
        instruction = self.step_text(step, "instruction", state.language)
        action = self.bot.conversations.new_action(title, instruction, source.get("url", ""))
        action.update(status="active", source_title=source.get("title", ""),
                      subject=plan["question"], completion=self.step_text(step, "completion", state.language),
                      plan_id=plan["id"], plan_step=step["id"], adjustment=adjustment)
        step["status"] = "active"
        state.action = action
        self.save(chat, state)
        return self.show_work(chat, state, step)

    def show_work(self, chat, state, step):
        plan, action = state.weekly_plan, state.action
        t = partial(ui_text, state.language)
        suffix = f"{plan['id']}:{action['id']}"
        status = step["status"]
        if status == "review":
            rows = [[button(label, f"plan:result:{suffix}:{key}")] for key, label in OUTCOMES.items()]
            return self.send(chat, f"<b>{t('Как получилось?')}</b>\n"
                             f"{h(self.step_text(step, 'completion', state.language))}", rows)
        if status in {"partial", "failed", "done"} and step["results"]:
            if self.valid_note(state):
                return self.ask_note(chat, state, step["results"][-1]["outcome"])
            return self.show_feedback(chat, state, step)
        rows = [[button("Сделано", f"action:done:{action['id']}"),
                 button("Не получается", f"action:blocked:{action['id']}")],
                [button("Напомнить", f"action:remind:{action['id']}"),
                 button("Отложить", f"plan:defer:{suffix}")],
                [button("К плану", "plan:open")]]
        if action["source"]:
            rows.insert(0, [link("Открыть источник", action["source"])])
        detail = self.step_details(step, state.language)
        if action.get("adjustment"):
            detail += f"\n\n<b>{t('Доработка')}</b>\n{h(action['adjustment'])}"
        return self.send(chat, detail, rows)

    def finish_action(self, chat, state, intent):
        step = self.linked_step(state)
        if state.weekly_plan.get("status") != "active" or not step or step["status"] != "active":
            return self.open(chat, state)
        if intent == "remind":
            return self.bot.begin_reminder(chat, state, state.action["title"], state.action)
        self.stop_current(chat, state, keep=True)
        step["status"] = "review"
        state.action["status"] = "review"
        step["execution"] = "completed" if intent == "done" else "attempted"
        self.save(chat, state)
        if intent == "blocked":
            return self.result(chat, state, step, "failed")
        return self.show_work(chat, state, step)

    def result(self, chat, state, step, outcome):
        plan, action = state.weekly_plan, state.action
        step["status"] = "done" if outcome == "success" else outcome
        action["status"] = "done" if outcome == "success" else "blocked"
        step["results"].append({"outcome": outcome, "execution": step["execution"],
                                "attempt_id": action["id"], "metrics": {}, "note": ""})
        if not self.current_step(plan):
            plan["status"] = "done"
        state.pending = {"kind": "weekly_note", "plan_id": plan["id"], "action_id": action["id"]}
        self.save(chat, state)
        return self.ask_note(chat, state, outcome)

    def ask_note(self, chat, state, outcome):
        plan, action = state.weekly_plan, state.action
        ask = ("Коротко опишите результат. Можно указать общие числа, например число заявок."
               if outcome == "success" else "Что помешало? Напишите коротко, чтобы подобрать доработку.")
        self.send(chat, ask, [[button("Пропустить", f"plan:skip:{plan['id']}:{action['id']}")]])

    def save_note(self, chat, state, step, note):
        plan = state.weekly_plan
        result = step["results"][-1]
        if note:
            result.update(note=note, metrics=aggregate_metrics(note))
        source = step.get("source", {})
        self.record_material(plan, source)
        following = self.current_step(plan)
        material = next_material(self.bot.catalog, plan["question"], state.profile,
                                 step=following if result["outcome"] == "success" else step,
                                 outcome=result["outcome"], note=note, used=plan.get("used", ()))
        if material:
            self.record_material(plan, material)
        result["material"] = material
        state.pending.clear()
        if not following:
            plan["status"] = "done"
        self.save(chat, state)
        return self.show_feedback(chat, state, step)

    @staticmethod
    def record_material(plan, material):
        used = plan.setdefault("used", [])
        for key in ("id", "url"):
            value = material.get(key)
            if value and value not in used:
                used.append(value)

    def show_feedback(self, chat, state, step):
        t = partial(ui_text, state.language)
        plan, action = state.weekly_plan, state.action
        suffix = f"{plan['id']}:{action['id']}"
        result = step["results"][-1]
        outcome = result["outcome"]
        lines = [f"<b>{t(OUTCOMES[outcome])}</b>"]
        if result.get("note"):
            lines.append(f"{t('Ваш результат')}: {h(result['note'])}")
        material = result.get("material")
        rows = []
        if outcome != "success":
            lines.append(h(adjustment_for(step, result.get("note", ""))))
            rows.append([button("Доработать действие", f"plan:retry:{suffix}")])
        elif following := self.current_step(plan):
            lines.append(f"<b>{t('Ближайшее действие')}</b>\n{h(self.step_text(following, 'instruction', state.language))}")
            rows.append([button("Следующее действие", f"plan:next:{suffix}")])
        else:
            lines.append(t("Все действия плана пройдены."))
        if material:
            reason = material.get("reason_en") if state.language == "en" else material.get("reason")
            lines.append(f"<b>{t('Подходящий материал')}</b>\n{h(material['title'])}")
            if reason:
                lines.append(h(reason))
            if material.get("price_display"):
                lines.append(h(t(material["price_display"])))
            if material.get("description"):
                lines.append(h(material["description"][:210]))
            if material.get("url"):
                rows.insert(0, [link("Открыть материал", material["url"])])
        else:
            lines.append(t("Подходящего нового материала пока нет."))
        rows.append([button("К плану", "plan:open")])
        self.send(chat, "\n\n".join(lines), rows)

    def message(self, chat, state, message):
        pending = state.pending
        if pending["kind"] == "weekly_capacity":
            seed = self.seed(state, pending["seed"])
            minutes = available_minutes(message)
            if not seed:
                return self.send(chat, "Этот совет уже недоступен. Откройте план или задайте новый вопрос.")
            if not minutes or minutes > 480:
                return self.send(chat, "Укажите разумное время в день, например «30 минут».")
            return self.build(chat, state, seed, minutes)
        if pending["kind"] == "weekly_details":
            seed = self.seed(state, pending["seed"])
            if not seed:
                return self.send(chat, "Этот совет уже недоступен. Откройте план или задайте новый вопрос.")
            try:
                details = prepare_plan_note(message, 280)
            except PrivacyRejected:
                return self.send(chat, "Опишите задачу без персональных сведений.")
            return self.build(chat, state, seed, pending["minutes"], details=details)
        if not self.valid_note(state):
            return self.open(chat, state)
        if state.weekly_plan.get("status") == "paused":
            return self.open(chat, state)
        try:
            note = prepare_plan_note(message)
        except PrivacyRejected:
            return self.send(chat, "Напишите только короткий итог и общие числа, без личных данных.",
                             [[button("Пропустить", f"plan:skip:{pending['plan_id']}:{pending['action_id']}")]])
        return self.save_note(chat, state, self.linked_step(state), note)

    def valid_note(self, state):
        pending = state.pending
        return (pending.get("kind") == "weekly_note" and self.linked_step(state) is not None
                and pending.get("plan_id") == state.weekly_plan.get("id")
                and pending.get("action_id") == state.action.get("id"))

    def callback(self, chat, state, payload):
        command, _, data = payload.partition(":")
        identifier, _, tail = data.partition(":")
        item, _, extra = tail.partition(":")
        plan, offer = state.weekly_plan, state.plan_offer
        if command == "open":
            return self.open(chat, state)
        if command == "draft":
            return self.draft(chat, state, identifier)
        if command == "time":
            if state.pending == {"kind": "weekly_capacity", "seed": identifier} and item in {"15", "20", "30", "60"}:
                return self.message(chat, state, f"{item} минут")
            return self.open(chat, state)
        if offer and offer["id"] == identifier:
            return self.offer_callback(chat, state, offer, command, item)
        if not plan or plan["id"] != identifier:
            return self.send(chat, "Эта кнопка относится к предыдущему плану.")
        if command in {"pause", "resume", "cancel"}:
            return self.manage(chat, state, plan, command)
        linked = self.linked_step(state)
        if linked and state.action["id"] == item and plan["status"] in {"active", "done"}:
            if command == "step":
                return self.show_work(chat, state, linked)
            if command == "skip" and self.valid_note(state):
                return self.save_note(chat, state, linked, "")
        if plan["status"] != "active":
            return self.open(chat, state)
        step = self.current_step(plan)
        if command == "begin" and step and step["id"] == item and step["status"] in {"todo", "deferred"}:
            if self.valid_note(state):
                return self.send(chat, "Сначала запишите результат или пропустите этот вопрос.",
                                 [[button("Пропустить", f"plan:skip:{plan['id']}:{state.action['id']}")]])
            return self.begin(chat, state, step)
        if not linked or state.action["id"] != item:
            return self.send(chat, "Эта кнопка относится к предыдущему действию.")
        if command == "result" and linked["status"] == "review" and extra in OUTCOMES:
            return self.result(chat, state, linked, extra)
        if command == "defer" and linked["status"] == "active":
            linked["status"] = "deferred"
            self.stop_current(chat, state)
            self.save(chat, state)
            return self.show(chat, state, plan, "Действие отложено. Вернитесь к нему, когда будет удобно.")
        if command == "retry" and linked["status"] in {"partial", "failed"} and not self.valid_note(state):
            return self.begin(chat, state, linked, adjustment_for(linked, linked["results"][-1].get("note", "")))
        return self.show(chat, state, plan)

    def offer_callback(self, chat, state, offer, command, token):
        if command == "offer":
            return self.show(chat, state, offer)
        if command == "discard":
            state.plan_offer = {}
            self.save(chat, state)
            return self.send(chat, "Предложение плана закрыто.", [[button("К плану", "plan:open")]])
        target = [state.weekly_plan.get("id"), state.action.get("id")]
        replace_required = (state.weekly_plan.get("status") in OPEN_PLANS
                            or state.action.get("status") in {"active", "blocked", "review"})
        if command == "replace" and (offer.get("confirmation") != token or offer.get("target") != target):
            return self.send(chat, "Сначала откройте новое предложение и подтвердите замену.",
                             [[button("Посмотреть новый план", f"plan:offer:{offer['id']}")]])
        if command == "activate" and replace_required:
            offer.update(confirmation=uuid4().hex[:10], target=target)
            self.save(chat, state)
            keep = "plan:open" if state.weekly_plan.get("status") in OPEN_PLANS else "action:show"
            return self.send(chat, "У вас уже есть план или задача. Заменить их новым планом?",
                             [[button("Заменить и начать", f"plan:replace:{offer['id']}:{offer['confirmation']}"),
                               button("Оставить как есть", keep)]])
        if command in {"activate", "replace"}:
            self.stop_current(chat, state)
            state.pending.clear()
            offer.pop("confirmation", None)
            offer.pop("target", None)
            offer["status"] = "active"
            state.weekly_plan, state.plan_offer = offer, {}
            self.save(chat, state)
        return self.open(chat, state)

    def manage(self, chat, state, plan, command):
        status = plan["status"]
        notice = ""
        if command == "pause" and status == "active":
            # Keep any result awaiting optional notes available until the user resumes.
            pending = dict(state.pending) if self.valid_note(state) else {}
            self.stop_current(chat, state, keep=True)
            if pending:
                state.pending = pending
            plan["status"] = "paused"
            notice = "Напоминания по действию выключены. После продолжения их можно поставить снова."
        elif command == "resume" and status == "paused":
            plan["status"] = "active"
        elif command == "cancel" and status in OPEN_PLANS:
            plan["status"] = "cancelled"
            self.stop_current(chat, state)
        self.save(chat, state)
        return self.show(chat, state, plan, notice)
