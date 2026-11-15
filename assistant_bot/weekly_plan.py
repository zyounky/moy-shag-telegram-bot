"""Build bounded plans from public catalog text and select step-specific follow-ups."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from .advice import catalog_context
from .answer_policy import general_staff_kpi
from .knowledge import OFFICIAL_HOST, PREFIXES
from .privacy import PrivacyRejected, _sanitize
from .query_language import russian_search_query
from .weekly_actions import ACTIONS, LOOKUPS, TOPICS, topic_for
from .weekly_sessions import schedule, sessions_for


_DEFINITION = re.compile(r"^(?:что такое|что значит|что означает|определение|расшифруй|what (?:is|are))\b", re.I)
_TASK = re.compile(
    r"\b(?:как|нуж(?:но|на|ны|ен)|хочу|помоги|план|цель|с чего|на неделю|how|plan|want|need|help)\b"
    r"|\b(?:провер|запуст|оцен|измер|настро|увелич|улучш|свер|состав|подобра|наня|сниз|привле)\w*", re.I,
)
_SPACE = re.compile(r"\s+")
_QUANTITY = r"(\d{1,3})\s*(мин(?:ут\w*)?|час\w*|minutes?|mins?|hours?)\b"
_DAILY = re.compile(rf"{_QUANTITY}\s*(?:в день|ежедневно|(?:a|per|each)\s+day|daily)", re.I)
_WEEKLY = re.compile(rf"{_QUANTITY}\s*(?:(?:на|в)\s+недел\w*|(?:a|per)\s+week|weekly)", re.I)
_STATED_CAP = re.compile(rf"(?:только|не больше|не более|максимум|могу уделить|at most|only)\s+{_QUANTITY}", re.I)
_NO_DATA = re.compile(
    r"(?:нет|без) (?:прошлой |исторической )?(?:статистики|данных(?: за прошлое| о прошлых| вообще))"
    r"|(?:исторических|прошлых) данных нет|не (?:сохранил|собирал|в[её]л)\w* статистик"
    r"|нечего сравнивать|no historical data", re.I,
)
_NO_TRACKING = re.compile(r"(?:не (?:записывал|учитывал|отслеживал|считал)\w*|нет (?:уч[её]та|статистики по))"
                          r"\s+(?:раньше\s+)?(?:заяв|заказ|визит|отклик)|нет доступа к рекламн"
                          r"|(?:заявки|заказы|визиты)\s+(?:раньше\s+)?не (?:записывал|учитывал|отслеживал|считал)", re.I)
_TRACKED = re.compile(r"(?:уже\s+(?:есть|настроил\w*|записал\w*)|известн\w*)[^.!?]{0,40}"
                      r"(?:промокод|метк\w+|аналитик\w+|цен\w+ заказа)", re.I)
_SPEND = re.compile(r"без (?:бюджета|платн\w+|затрат)|нет (?:денег|бюджета)|no budget", re.I)
_ALTERNATIVE_AD_METRIC = re.compile(r"\b(?:cpa|cpl|ctr|roas|cac|cpc)\b|стоимост\w+ заявк|кликабельност|цен\w+ (?:заявк|лид)", re.I)
_AD_GOALS = (
    (r"\b(?:cpo)\b|стоимост\w+ заказ|(?:число|количество|считать|посчитат\w*)\s+заказ", "число заказов"),
    (r"\b(?:cpl)\b|стоимост\w+ заявк|(?:число|количество|считать|посчитат\w*)\s+заяв", "число заявок"),
    (r"\bctr\b|кликабельност", "показы и клики для расчёта CTR"),
    (r"\broas\b", "расходы и связанная выручка для расчёта ROAS"),
)
_LEARN = re.compile(r"(?:хочу|нужен|подбери|найди|посоветуй|порекомендуй|пройти|want|need)"
                    r"[^.!?]{0,65}\b(?:курс|обучени|course|training)\w*|системн\w+ обучени", re.I)
_FREE_ONLY = re.compile(r"бесплатн|без покупк|не (?:нужен|хочу) платн|free only|no paid", re.I)
_TEMPLATE = re.compile(r"\b(?:шаблон|бланк|образец|template)\w*", re.I)
_BLOCKED = re.compile(r"нет (?:доступ|данн|статистик|расход|сумм)|не найд[её]н|не наш[её]л|не хватает|пробел|no (?:data|access)", re.I)
_LOW_TIME = re.compile(r"врем[ея]|не успева|time", re.I)
_SALES_ROLE = re.compile(r"(?:продавц|менеджер\w* по продажам|команд\w* продаж)", re.I)
_NICHE_TERMS = (r"одежд", r"ресторан|общепит|кафе|кофейн", r"спорте|фитнес",
                r"маркетплейс|\bozon\b|wildberries", r"франшиз")
_PUBLIC_TEXT = {"public_text", "free_lesson"}
_PUBLIC_CARD = _PUBLIC_TEXT | {"description", "document_description"}
_DAYS = {1: (1,), 2: (1, 4), 3: (1, 3, 6), 4: (1, 2, 4, 7), 5: (1, 2, 3, 5, 7)}


def _short(text: str, limit: int) -> str:
    clean = _SPACE.sub(" ", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def offerable(question: str) -> bool:
    """Offer a plan for a business task, including a task following a definition."""
    value = question.strip()
    if _DEFINITION.search(value) and not re.search(r"(?:[.!?]\s*|\bи\s+)как\b|\band how\b", value, re.I):
        return False
    return len(re.findall(r"\w+", value)) >= 3 and bool(_TASK.search(value) or topic_for(value) == "kpi")


def _search(catalog, question: str, profile: dict):
    for limit in (_DAILY, _WEEKLY, _STATED_CAP):
        question = limit.sub("", question)
    normalized = "\n".join(russian_search_query(line) for line in question.splitlines())
    return catalog.search(normalized, catalog_context(profile))


def _record(material, *, demo: bool) -> dict:
    kind = material.kind
    url = material.url
    if kind not in PREFIXES or getattr(material, "content_scope", "description") not in _PUBLIC_CARD:
        return {}
    if url:
        parsed = urlsplit(url)
        if (demo or parsed.scheme != "https" or parsed.netloc != OFFICIAL_HOST
                or not parsed.path.startswith(PREFIXES[kind])):
            return {}
    elif not demo:
        return {}
    try:
        title = _short(_sanitize(material.title), 160)
    except PrivacyRejected:
        return {}
    if not title:
        return {}
    try:
        description = _short(_sanitize(getattr(material, "description", "")), 235)
    except PrivacyRejected:
        description = ""
    price_display = getattr(material, "price_display", "")
    if not isinstance(price_display, str) or not re.fullmatch(r"Бесплатно|По подписке|[0-9][0-9 ,.]*\s*₽", price_display):
        price_display = ""
    identifier = getattr(material, "material_id", "") or url
    if not identifier:
        identifier = "demo-" + hashlib.sha256(f"{kind}\0{material.title}".encode()).hexdigest()[:20]
    return {
        "id": identifier, "title": title, "url": url, "kind": kind, "description": description,
        "price_display": price_display, "paid": getattr(material, "is_paid", False),
    }


def _duration_bounds(context: str, minutes) -> tuple[int, int]:
    def amount(match):
        return int(match[1]) * (60 if match[2].lower().startswith(("час", "hour")) else 1)

    daily = _DAILY.search(context) or _STATED_CAP.search(_WEEKLY.sub("", context))
    weekly = _WEEKLY.search(context)
    selected = minutes if type(minutes) is int and minutes > 0 else 25
    per_day = min(selected, amount(daily)) if daily else selected
    return amount(weekly) if weekly else 7 * per_day, per_day


def _applicable_title(title, query, topic, profile):
    title = re.sub(r"\s+", " ", title)
    if topic == "kpi" and general_staff_kpi(query) and _SALES_ROLE.search(title):
        return False
    if topic in {"retention", "hiring", "kpi"} and _SALES_ROLE.search(title) and not _SALES_ROLE.search(query):
        return False
    if profile.get("stage") == "launch" and re.search(r"действующ\w+\s+(?:бизнес|кофейн|магазин)", title, re.I):
        return False
    context = f"{query} {profile.get('sector', '')}"
    return not any(re.search(niche, title, re.I) and not re.search(niche, context, re.I)
                   for niche in _NICHE_TERMS)


def _support(action, evidence):
    text = _SPACE.sub(" ", evidence.passage).strip()
    match = re.search(action.evidence, text, re.I)
    if not match:
        return ""
    start = max(0, match.start() - 35)
    end = min(len(text), match.end() + 105)
    return _sanitize(text[start:end].strip())


def _sources(catalog, query, profile, topic):
    demo = bool(getattr(catalog, "is_demo", False))
    lookup = LOOKUPS.get(topic)
    base_question = query.partition("?")[0]
    searches = list(dict.fromkeys(filter(None, (query, base_question, lookup if not demo else None))))
    keys = set()
    for search in searches:
        for evidence in _search(catalog, search, profile).evidence:
            if (evidence.content_scope not in _PUBLIC_TEXT
                    or not _applicable_title(evidence.title, query, topic, profile)):
                continue
            record = _record(evidence, demo=demo)
            if not record:
                continue
            key = (record["id"], evidence.passage)
            if key not in keys:
                keys.add(key)
                yield evidence, record


def _allowed(action, topic, context, stage):
    if topic == "ad":
        if action.subject == "cost_per_lead" and not re.search(r"заяв|обращен|\bcpl\b", context, re.I):
            return False
        if _ALTERNATIVE_AD_METRIC.search(context) and action.subject == "cost_per_order":
            return False
        if stage == "launch" or _NO_DATA.search(context):
            return action.kind not in {"calculate", "compare"}
        if action.kind == "instrument" and _TRACKED.search(context) and not _NO_TRACKING.search(context):
            return False
        if _NO_TRACKING.search(context) and action.kind in {"calculate", "compare"}:
            return False
        if action.subject == "cost_per_order" and "число заявок" == _advertising_goal(context):
            return False
    if topic == "romi" and (stage == "launch" or _NO_DATA.search(context)) and action.kind == "calculate":
        return False
    return True


def _advertising_goal(context):
    return next((goal for pattern, goal in _AD_GOALS if re.search(pattern, context, re.I)), "")


def _make_step(action, evidence, record, excerpt, *, future=False, no_past=False, ad_goal=""):
    title = action.title
    instruction, completion = action.instruction, action.completion
    if action.subject == "campaign_metric" and ad_goal:
        instruction = f"Запишите одну кампанию, её цель, даты наблюдения и выбранный показатель: {ad_goal}."
    if action.subject == "campaign_inputs" and future:
        title = "Подготовьте журнал для кампании"
        instruction = ("Подготовьте журнал для одной кампании: даты, расходы, выбранное целевое действие "
                       "и источник для каждой цифры. Исторические пробелы оставьте пустыми.")
        completion = "Есть форма будущего учёта с датами и источниками данных. Неподтверждённые числа не внесены."
    if action.subject == "campaign_finances" and no_past:
        title = "Подготовьте учёт затрат и продаж для ROMI"
        instruction = ("Для одной кампании подготовьте таблицу с датами и колонками для рекламных расходов, "
                       "связанных продаж и переменных затрат по ним. Укажите способ связать будущую продажу с кампанией.")
        completion = "Есть форма учёта с источниками данных и способом связать продажу с кампанией."
    return {
        "title": title, "why": action.why, "instruction": instruction, "completion": completion,
        "minutes": action.minutes, "source": {**record, "heading": evidence.heading, "excerpt": excerpt},
        "kind": action.kind, "subject": action.subject, "artifact": action.artifact,
        "requires": list(action.requires), "search_terms": action.search_terms, "reduced": action.draft_only,
    }


def build_plan(catalog, question: str, profile: dict, *, advice=None, obstacle: str = "", minutes=None) -> dict:
    """Compose distinct actions, preserving source, time and prerequisite constraints."""
    result = {"goal": "", "days": 7, "steps": [], "incomplete": True,
              "clarification": "", "clarification_en": "", "limitation": ""}
    try:
        goal, limitation = _sanitize(question), _sanitize(obstacle)
    except PrivacyRejected:
        return {**result, "clarification": "Опишите задачу без персональных сведений.",
                "clarification_en": "Describe your task without personal details."}
    result["goal"] = _short(goal, 300)
    context = " ".join((goal, limitation))
    topic = topic_for(goal)
    if not offerable(goal) or not topic:
        return {**result, "clarification": "Какой конкретный результат нужен на этой неделе?",
                "clarification_en": "What specific result do you need this week?"}

    available, per_day = _duration_bounds(context, minutes)
    sources = list(_sources(catalog, goal, profile, topic))
    steps, signatures, artifacts = [], set(), set()
    public_fragments = getattr(catalog, "public_fragments", None)
    no_past = profile.get("stage") == "launch" or bool(_NO_DATA.search(context))
    time_limited = False
    session_count = 0
    for action in ACTIONS[topic]:
        signature = (action.kind, action.subject, action.artifact)
        if signature in signatures or action.artifact in artifacts:
            continue
        if not _allowed(action, topic, context + " " + profile.get("goal", ""), profile.get("stage")):
            continue
        if not set(action.requires) <= artifacts:
            continue
        supporting = None
        pattern = re.compile(action.evidence, re.I)
        for evidence, record in sources:
            candidates = (*public_fragments(evidence, pattern), evidence) if public_fragments else (evidence,)
            for candidate in candidates:
                try:
                    excerpt = _support(action, candidate)
                except PrivacyRejected:
                    continue
                if excerpt:
                    supporting = (candidate, record, excerpt)
                    break
            if supporting:
                break
        if not supporting:
            continue
        sessions = sessions_for(action, per_day, future=no_past)
        if not sessions or action.minutes > available or session_count + len(sessions) > 7:
            time_limited = True
            continue
        step = _make_step(action, *supporting, future=no_past or bool(_NO_TRACKING.search(context)), no_past=no_past,
                          ad_goal=_advertising_goal(f"{context} {profile.get('goal', '')}") if topic == "ad" else "")
        # Single-session text can be adapted by _make_step (e.g. future data).
        if len(sessions) == 1:
            sessions[0].update(title=step["title"], instruction=step["instruction"])
        step.update(sessions=sessions, total_minutes=action.minutes,
                    minutes=max(session["minutes"] for session in sessions))
        steps.append(step)
        signatures.add(signature)
        if not action.draft_only:
            artifacts.add(action.artifact)
        available -= action.minutes
        session_count += len(sessions)
        if len(steps) == 5:
            break

    if not steps:
        if time_limited:
            return {**result, "clarification": "На подтверждённые действия нужно больше времени. Какую часть задачи хотите выделить?",
                    "clarification_en": "The sourced actions need more time. Which part of the task would you like to focus on?"}
        return {**result, "clarification": "В каталоге пока мало инструкций для плана. Что известно о вашей задаче сейчас?",
                "clarification_en": "The catalog has too few instructions for a plan. What do you already know about your task?"}
    schedule(steps)
    if time_limited or topic == "opening":
        result["limitation"] = ("Это черновик части бизнес-плана. Полный анализ рынка и финансовый прогноз потребуют отдельных проверок."
                                if topic == "opening" else "Действия, которые требуют более длинной сессии, перенесены за пределы этого плана.")
    result.update(steps=steps, incomplete=len(steps) < 3 or time_limited or topic == "opening", topic=topic)
    return result


def adjustment_for(step, note):
    """Keep retries on the current artifact and adapt the work to the stated blocker."""
    subject = step.get("subject")
    if _BLOCKED.search(note):
        if (subject in {"campaign_metric", "campaign_attribution", "campaign_inputs", "cost_per_order", "cost_per_lead", "campaign_comparison", "campaign_finances", "campaign_romi"}
                and re.search(r"расход|затрат|сумм", note, re.I)):
            return ("Для той же кампании и периода проверьте, где сохранилась сумма рекламных расходов. "
                    "Запишите подтверждённую сумму с источником или конкретный пробел. "
                    "Не заменяйте неизвестную сумму нулём; расчёт продолжите после проверки входных данных.")
        if subject in {"campaign_metric", "campaign_attribution", "campaign_inputs", "cost_per_order", "cost_per_lead", "campaign_comparison"}:
            return ("Проверьте один доступный показатель по своей кампании: продажи, заявки или расходы. "
                    "Укажите источник сохранившейся цифры и какое значение ещё нужно получить. "
                    "Если его нельзя восстановить, внесите колонку для будущего учёта.")
        if subject in {"campaign_finances", "campaign_margin", "campaign_romi", "period_costs", "margin_fraction", "break_even_revenue"}:
            return ("Выпишите известные суммы и даты из одного доступного отчёта. Отметьте недостающий вход для расчёта "
                    "и где его проверить. Рассчитайте показатель после заполнения пробела.")
        if subject in {"repeat_purchases", "repeat_interval"}:
            return "Проверьте историю заказов одной группы. Если повторные покупки там не видны, подготовьте колонки для их будущего учёта."
        if subject in {"team_feedback", "team_development", "team_change"}:
            return "Подготовьте один вопрос о ситуации в команде и согласуйте способ получить ответ без публикации имён."
        if subject in {"demand_interview", "demand_offer", "foot_traffic", "menu_test"}:
            return "Выберите одну доступную группу или точку наблюдения. Запишите, какой отклик сможете собрать и какого пока не хватает."
    if _LOW_TIME.search(note):
        return f"В один короткий подход подготовьте черновик результата шага «{step['title']}». Сохраните его и отметьте, какую часть ещё нужно проверить."
    return f"Вернитесь к критерию шага. {step['completion']} Проверьте самый существенный недостающий пункт."


def next_material(catalog, question: str, profile: dict, *, step=None, outcome: str = "success",
                  note: str = "", used=(), learning_intent: bool = False) -> dict | None:
    """Recommend a new card only when its content or metadata relates to a specific action."""
    if not question or not step:
        return None
    try:
        goal, safe_note = _sanitize(question), _sanitize(note)
    except PrivacyRejected:
        return None
    topic = topic_for(goal)
    if not topic:
        return None
    demo = bool(getattr(catalog, "is_demo", False))
    source = step.get("source") or {}
    used_keys = set(used) | {source.get("url"), source.get("id")}
    explicit_template = bool(_TEMPLATE.search(safe_note)) and not re.search(r"без шаблон|не нужен шаблон", safe_note, re.I)
    learning = learning_intent or bool(_LEARN.search(f"{goal} {safe_note}"))
    paid_allowed = learning and not (_SPEND.search(f"{goal} {safe_note}") or _FREE_ONLY.search(f"{goal} {safe_note}"))
    focus = step.get("search_terms", "")
    if outcome != "success" and _BLOCKED.search(safe_note) and topic == "ad":
        focus = r"расход|уч[её]т|статистик|метк\w*|промокод"
    searches = [_search(catalog, goal, profile)]
    if safe_note and (explicit_template or learning) and re.search(TOPICS[topic], safe_note, re.I):
        searches.insert(0, _search(catalog, safe_note, profile))

    cards = []
    for search in searches:
        for item in (*search.recommendations, *search.evidence):
            card = _record(item, demo=demo)
            if not card or {card["id"], card["url"]} & (used_keys - {"", None}):
                continue
            if not _applicable_title(card["title"], goal, topic, profile):
                continue
            if (card["paid"] and (not paid_allowed or card["kind"] != "course")
                    or explicit_template and card["kind"] != "document"):
                continue
            description = f"{card['title']} {card['description']}"
            if not re.search(TOPICS[topic], description, re.I):
                continue
            if not (focus and re.search(focus, description, re.I) or learning and card["kind"] == "course"):
                continue
            cards.append(card)
    if not cards:
        return None
    if paid_allowed and not explicit_template:
        cards.sort(key=lambda card: not card["paid"])
    card = cards[0]
    title = step["title"]
    if card["paid"]:
        reason = f"Вы запросили системное обучение по теме шага «{title}». Доступна карточка курса."
        reason_en = f"You asked for structured training on the topic of the step “{title}”. This is the course listing."
    elif (outcome != "success" and re.search(r"расход|затрат", safe_note, re.I)
          and re.search(r"расход|затрат", f"{card['title']} {card['description']}", re.I)):
        reason = f"Вы указали пробел в расходах. В карточке есть сведения об их учёте для доработки шага «{title}»."
        reason_en = f"You reported a gap in spending data. The listing covers tracking expenses for the step “{title}”."
    elif card["kind"] == "document":
        reason = f"Описание шаблона относится к учёту для шага «{title}»."
        reason_en = f"The template description relates to the record keeping in the step “{title}”."
    else:
        reason = f"Тема материала совпадает с задачей шага «{title}»."
        reason_en = f"The material covers the topic of the step “{title}”."
    return {**card, "reason": reason, "reason_en": reason_en, "repeated": False}
