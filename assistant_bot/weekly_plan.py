"""Build locally grounded plans and choose a follow-up catalog material."""

from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

from .advice import catalog_context
from .knowledge import OFFICIAL_HOST, PREFIXES, _tokens
from .privacy import PrivacyRejected, _sanitize
from .query_language import russian_search_query


_DEFINITION = re.compile(r"^(?:что такое|что значит|что означает|определение|расшифруй|what (?:is|are))\b", re.I)
_TASK = re.compile(
    r"\b(?:как|нужно|хочу|помоги|план|цель|с чего|на неделю|how|plan|want|need|help)\b"
    r"|\b(?:провер|запуст|оцен|измер|настро|увелич|улучш|свер|состав|подобра|наня|сниз|привле)\w*", re.I,
)
_ACTION = re.compile(
    r"\b(?:выберите|опишите|запишите|отметьте|сравните|проверьте|составьте|сделайте|заведите|посчитайте|"
    r"рассчитайте|разделите|предложите|добавьте|выгрузите|занесите|подготовьте|поговорите|определите|"
    r"уточните|проанализируйте|отслеживайте|поставьте|зафиксируйте|выделите|проведите|спросите|"
    r"проверь|сравни|посчитай|составь|запиши|соберите|собери|choose|record|check|compare|calculate)\b", re.I,
)
_STEP_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|\s+[•●]\s+")
_NUMBERED_STEP = re.compile(r"(?m)^[ \t]*шаг\s+\d+[.):–—-][ \t]+(?P<title>[^\n]{12,170})$", re.I)
_SPACE = re.compile(r"\s+")
_HAS_LINK = re.compile(r"https?://|www\.|tg://|t\.me/|<[^>]*>", re.I)
_REGULATED = re.compile(r"налог|пошлин|деклараци|\b(?:усн|ндс|псн)\b", re.I)
_TERM_DATE = re.compile(r"\b(?:срок|до|не позднее)\s+\d|[0-9]{1,2}[./][0-9]{1,2}[./][0-9]{2,4}", re.I)
_ADVERTISING = re.compile(r"\b(?:реклам|кампани|объявлен)\w*|\b(?:ads?|advertising|campaigns?)\b", re.I)
_LIMITED_AD_SPEND = re.compile(r"без (?:бюджета|затрат|платной рекламы)|нет (?:денег|бюджета)", re.I)
_AD_SPEND = re.compile(r"\b(?:оплатите|купите|потратьте|вложите|пополните|увеличьте бюджет)\b", re.I)
_AD_METRIC = re.compile(r"\b(?:выберите|определите|задайте)\b[^.!?]{0,90}\b(?:показател|метрик|цел|действи)", re.I)
_AD_INSTRUMENT = re.compile(r"\b(?:заведите|создайте|добавьте|разметьте|настройте|включите)\b", re.I)
_AD_MEASURE = re.compile(r"\b(?:сравните|посчитайте|разделите|рассчитайте|оцените|сопоставьте)\b", re.I)
_AD_MEASUREMENT_SOURCE = re.compile(r"(?:эффективност|результат|метрик|показател)", re.I)
_AD_DATA = re.compile(r"источник\w* данн|статистик|аналитик|рекламн\w+ кабинет", re.I)
_AD_COMPARISON = re.compile(r"сравни|разн\w+\s+кампан|нескольк\w+\s+кампан", re.I)
_NO_DASHBOARD = re.compile(r"без доступа к .*кабинет|нет доступа к .*кабинет|no access to .*dashboard", re.I)
_UNAVAILABLE = re.compile(r"\b(?:нет|не хватает|не удалось|не получается|без доступа|не могу|no data|no access)\b", re.I)
_QUANTITY = r"(\d{1,3})\s*(мин(?:ут\w*)?|час\w*|minutes?|mins?|hours?)\b"
_WEEK_UNIT = r"(?:на|в)\s+недел\w*|(?:a|per)\s+week|weekly"
_TIME = re.compile(
    r"\b(?:(?:у меня|у нас)\s+)?(?:есть|только|не больше|не более|максимум|могу уделить)"
    rf"\s+{_QUANTITY}(?:\s+(?:{_WEEK_UNIT}))?"
    rf"|\b(?:(?:i have|we have|at most|only)\s+)?{_QUANTITY}\s+(?:{_WEEK_UNIT})", re.I,
)
_DAILY_TIME = re.compile(
    r"\b(?:(?:(?:у меня|у нас)\s+)?(?:есть|только|не больше|не более|максимум|могу уделить|"
    r"i have|we have|at most|only)\s+)?(?:по\s+)?"
    rf"{_QUANTITY}\s+(?:в день|ежедневно|(?:a|per)\s+day|daily)", re.I,
)
_TRIED = re.compile(
    r"\b(?:уже\s+(?:сделал\w*|пробовал\w*|посчитал\w*|составил\w*|собрал\w*|проверил\w*|настроил\w*))"
    r"\s+([^.!?]{5,100})", re.I,
)
_TEMPLATE = re.compile(r"\b(?:шаблон|бланк|образец|template|working document)\b", re.I)
_NO_TEMPLATE = re.compile(r"\b(?:не нужен|без|не ищу|no)\s+(?:шаблон\w*|бланк\w*|template)", re.I)
_LEARNING = re.compile(
    r"\b(?:хочу|нужен|подбери|найди|посоветуй|порекомендуй|пройти|изучить|need|want|recommend)"
    r"\b[^.!?]{0,55}\b(?:курс\w*|обучени\w*|course|training)\b"
    r"|\b(?:подробн\w*|системн\w*|углубл\w*)\s+(?:курс\w*|обучени\w*)", re.I,
)
_FREE_ONLY = re.compile(
    r"бесплатн|без (?:бюджета|покупки)|не (?:нужен|хочу)(?:\s+\w+){0,2}\s+(?:платн\w*|курс\w*)"
    r"|free only|no paid", re.I,
)
_PUBLIC_TEXT = {"public_text", "free_lesson"}
_PUBLIC_CARD = _PUBLIC_TEXT | {"description", "document_description"}
_DAYS = {3: (1, 3, 7), 4: (1, 2, 4, 7), 5: (1, 2, 3, 5, 7)}
_LEARNING_WORDS = {"пройти", "изучить", "посоветовать", "порекомендовать", "подобрать", "найти",
                   "курс", "обучение", "подробный", "системный", "углубленный", "платный",
                   "course", "training", "recommend", "want", "need", "paid", "посоветуй", "порекомендуй"}
_METRICS = {"заказ", "звонок", "бронь", "заявка", "визит"}


def _short(text: str, limit: int) -> str:
    clean = _SPACE.sub(" ", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def offerable(question: str) -> bool:
    """Offer plans for tasks, including follow-ups after a short definition."""
    value = question.strip()
    if _DEFINITION.search(value) and not re.search(r"(?:[.!?]\s*|\bи\s+)как\b|\band how\b", value, re.I):
        return False
    return len(re.findall(r"\w+", value)) >= 3 and bool(_TASK.search(value))


def _search(catalog, question: str, profile: dict):
    question = _TIME.sub("", _DAILY_TIME.sub("", question)).rstrip(" \t.")
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
        # The original demo fixture predates IDs and intentionally has no external links.
        identifier = "demo-" + hashlib.sha256(f"{kind}\0{material.title}".encode()).hexdigest()[:20]
    return {
        "id": identifier,
        "title": title,
        "url": url,
        "kind": kind,
        "description": description,
        "price_display": price_display,
        "paid": getattr(material, "is_paid", False),
    }


def _step(title, why, instruction, completion, source, *, english=None):
    step = {
        "title": title,
        "why": why,
        "instruction": _short(instruction, 260),
        "completion": completion,
        "source": source,
    }
    if english:
        step.update(zip(("title_en", "why_en", "instruction_en", "completion_en"), english))
    return step


def _action_key(instruction: str) -> set[str]:
    return set(_tokens(instruction))


def _duplicate(instruction: str, steps: list[dict]) -> bool:
    return any(_similar(instruction, step["instruction"]) for step in steps)


def _similar(first: str, second: str) -> bool:
    first_terms, second_terms = _action_key(first), _action_key(second)
    size = max(len(first_terms), len(second_terms))
    return size > 0 and len(first_terms & second_terms) / size >= 0.65


def _instructions(evidence, question: str):
    headings = {_short(match["title"], 170) for match in _NUMBERED_STEP.finditer(evidence.passage)}
    passage = _NUMBERED_STEP.sub(lambda match: match["title"], evidence.passage)
    for sentence in _STEP_SPLIT.split(passage):
        sentence = re.sub(r"^\s*(?:[-–—]|\d+[.)])\s*", "", sentence).strip()
        if (not 18 <= len(sentence) <= 250 or sentence.endswith("?") or _HAS_LINK.search(sentence)
                or (sentence not in headings and not _ACTION.search(sentence))):
            continue
        if _REGULATED.search(question) and _TERM_DATE.search(sentence):
            continue
        if _LIMITED_AD_SPEND.search(question) and _AD_SPEND.search(sentence):
            continue
        if _NO_DASHBOARD.search(question) and re.search(r"рекламн\w+\s+кабинет", sentence, re.I):
            continue
        try:
            yield _sanitize(sentence)
        except PrivacyRejected:
            continue


def _ad_instructions(evidence, context: str, profile: dict) -> list[dict]:
    """Turn explanatory measurement text into a small check, without inventing a method."""
    text = evidence.passage
    if not (_ADVERTISING.search(evidence.title) and _AD_MEASUREMENT_SOURCE.search(evidence.title)):
        return []
    options = []
    if _AD_DATA.search(text):
        options.append(_step(
            "Найдите данные для проверки", "Материал связывает цели кампании с источниками данных.",
            "Для выбранного показателя найдите один доступный источник данных. Запишите, что в нём видно и чего нет.",
            "Указан источник данных или отмечено, что данных пока нет.", {},
            english=("Find data for your check", "The material links campaign goals to data sources.",
                     "Find one available source for your chosen metric. Note what it can and cannot show.",
                     "You have named a source or recorded that the data is missing."),
        ))
    if _AD_COMPARISON.search(text) and not _UNAVAILABLE.search(context):
        wording = ("Для будущего теста запишите, какие результаты по одному показателю сможете сравнить."
                   if profile.get("stage") == "launch" else
                   "Сравните доступные результаты по одному показателю и за сопоставимый период. "
                   "Если данных не хватает, запишите пробел и не делайте вывод о влиянии рекламы.")
        options.append(_step(
            "Проверьте сравнимость результатов", "Одна цифра не показывает, какая реклама привела клиентов.",
            wording, "Есть сравнение по одному показателю либо отмечено, каких данных не хватает.", {},
            english=("Check whether the results are comparable", "One number cannot show which campaign brought customers.",
                     "Compare results for the same metric over similar periods or note which data is missing.",
                     "You have made the comparison or recorded the missing data."),
        ))
    return options


def _seed_step(advice, evidence, source):
    if isinstance(advice, dict):
        action = advice.get("action") or advice.get("title")
        completion = advice.get("completion", "")
        excerpt = advice.get("passage", "")
        label = advice.get("source_title", "")
        url = advice.get("source") or advice.get("url", "")
    else:
        action = getattr(advice, "action", "")
        completion = getattr(advice, "completion", "")
        excerpt = getattr(advice, "passage", "")
        label = getattr(advice, "title", "")
        url = getattr(advice, "url", "")
    if label != evidence.title or url != evidence.url:
        return None
    # Keep a previous validated suggestion only if its quote still matches this source.
    if not isinstance(excerpt, str) or not isinstance(action, str) or not isinstance(completion, str):
        return None
    excerpt = _SPACE.sub(" ", excerpt).strip().rstrip("…")
    if len(excerpt) < 35 or not 20 <= len(action) <= 180 or _HAS_LINK.search(action) or _HAS_LINK.search(completion):
        return None
    source_text = _SPACE.sub(" ", evidence.passage)
    try:
        if excerpt not in source_text and excerpt not in _SPACE.sub(" ", _sanitize(evidence.passage)):
            return None
        action, completion = _sanitize(action), _sanitize(completion)
    except PrivacyRejected:
        return None
    return _step("Выполните предложенное действие", "Это сохраняет следующий шаг из консультации.",
                 action, _short(completion, 180) or "Записано, что удалось сделать.", source)


def _duration_bounds(question: str, minutes) -> tuple[int, int]:
    daily = _DAILY_TIME.search(question)
    weekly = _TIME.search(_DAILY_TIME.sub("", question))
    per_day = (
        int(daily[1]) * (60 if daily[2].lower().startswith(("час", "hour")) else 1)
        if daily else 25
    )
    if type(minutes) is int and minutes >= 0:
        per_day = min(per_day, minutes)
    total = 60
    if weekly:
        amount, unit = (weekly[1], weekly[2]) if weekly[1] else (weekly[3], weekly[4])
        total = min(total, int(amount) * (60 if unit.lower().startswith(("час", "hour")) else 1))
    return min(total, 90), min(per_day, 25)


def _finish_steps(steps: list[dict], available: int, per_day: int) -> list[dict]:
    days = _DAYS[len(steps)]
    durations = (10, 15, 15, 15, 10) if len(steps) == 5 else (10, 15, 15, 10)
    fair_share, extra = divmod(available, len(steps))
    for index, step in enumerate(steps):
        step.update(id=str(index + 1), day=days[index],
                    minutes=min(durations[index], per_day, fair_share + (index < extra)))
    return steps


def build_plan(catalog, question: str, profile: dict, *, advice=None, obstacle: str = "", minutes=None) -> dict:
    """Use catalog instructions and local planning scaffolding, without calling a model."""
    result = {"goal": "", "days": 7, "steps": [], "incomplete": True,
              "clarification": "", "clarification_en": ""}
    try:
        goal = _sanitize(question)
        limitation = _sanitize(obstacle)
    except PrivacyRejected:
        return {**result, "clarification": "Опишите задачу без персональных сведений.",
                "clarification_en": "Describe your task without personal details."}
    result["goal"] = _short(goal, 300)
    if not offerable(goal):
        return {**result, "clarification": "Какого результата вы хотите добиться за неделю?",
                "clarification_en": "What outcome would you like to achieve this week?"}
    available, per_day = _duration_bounds(" ".join((goal, limitation)), minutes)
    if available < 9 or per_day < 3:
        return {**result, "clarification": "Какое одно короткое действие реально выполнить за доступное время?",
                "clarification_en": "What single short action can you realistically complete in your available time?"}

    demo = bool(getattr(catalog, "is_demo", False))
    found = _search(catalog, goal, profile)
    context = " ".join((goal, limitation))
    source = {}
    for evidence in found.evidence:
        if evidence.content_scope not in _PUBLIC_TEXT:
            continue
        record = _record(evidence, demo=demo)
        instructions = tuple(_instructions(evidence, context))
        ad_instructions = _ad_instructions(evidence, context, profile) if _ADVERTISING.search(goal) else []
        if record and (instructions or ad_instructions):
            source = record
            break
    if not source:
        return {**result, "clarification": "В каталоге не нашлось достаточно инструкций. Что уже пробовали и какой результат нужен?",
                "clarification_en": "The catalog did not provide enough instructions. What have you tried, and what result do you need?"}

    stage = profile.get("stage")
    ad_task = bool(_ADVERTISING.search(goal) and _ADVERTISING.search(source["title"]))
    if _UNAVAILABLE.search(context):
        setup = _step("Проверьте, что уже доступно", "Это позволит выбрать шаг, который можно начать сейчас.",
                      "Запишите, каких данных или ресурсов не хватает и что можно использовать вместо них.",
                      "Названо одно препятствие и один доступный ресурс.", source,
                      english=("Check what you already have", "This identifies a step you can start now.",
                               "Note what data or resources are missing and what you can use instead.",
                               "You have recorded one obstacle and one available resource."))
    elif ad_task:
        try:
            metric = _short(_sanitize(profile.get("goal") or ""), 60)
        except PrivacyRejected:
            metric = ""
        instruction = ("Для будущего рекламного теста запишите цель и показатель: " if stage == "launch"
                       else "Выберите одну действующую кампанию и запишите цель и показатель: " if stage == "operate"
                       else "Выберите одну кампанию и запишите цель и показатель: ")
        instruction += (metric or "возьмите один показатель из материала") + "."
        setup = _step("Выберите показатель рекламы", "Один показатель связывает результат с задачей.",
                      instruction, "Записан показатель и выбран период.", source,
                      english=("Choose an advertising metric", "One metric links the campaign to your task.",
                               "Pick a campaign or a planned test, then write down one metric and the period.",
                               "A metric and a period are recorded."))
    else:
        setup = _step("Зафиксируйте цель недели", "Конкретный результат поможет выбрать нужное действие.",
                      "Запишите один результат, который можно наблюдать, и что уже сделано по задаче.",
                      "В записи есть один результат и текущее состояние.", source,
                      english=("Set a goal for the week", "A specific outcome helps you choose the right action.",
                               "Write down one observable outcome and what you have already done.",
                               "Your note records one outcome and the current situation."))

    steps = [setup]
    target = min(5, max(3, available // 10))
    seed = _seed_step(advice, evidence, source) if advice else None
    if seed and ((_LIMITED_AD_SPEND.search(context) and _AD_SPEND.search(seed["instruction"]))
                 or (_REGULATED.search(goal) and _TERM_DATE.search(seed["instruction"]))):
        seed = None
    if ad_task:
        specific = [instruction for instruction in instructions if not _AD_METRIC.search(instruction)]
        goal_metrics = _action_key(profile.get("goal") or "") & _METRICS
        if goal_metrics:
            specific = [instruction for instruction in specific
                        if not (terms := _action_key(instruction) & _METRICS) or terms & goal_metrics]
        if specific:
            instructions = sorted(specific, key=lambda action: 0 if _AD_INSTRUMENT.search(action)
                                  else 2 if _AD_MEASURE.search(action) else 1)
        elif goal_metrics:
            instructions = ()
    attempted = [_action_key(match[1]) for match in _TRIED.finditer(context)]
    grounded_count = 0
    for instruction in instructions:
        if len(steps) == target - 1:
            break
        if not _duplicate(instruction, steps) and not any(
            len(done & _action_key(instruction)) >= max(2, len(done) * 0.8) for done in attempted
        ):
            if seed and _similar(instruction, seed["instruction"]):
                step = seed
                seed = None
            else:
                title = _short(instruction.split(":", 1)[0], 80)
                step = _step(title, "Это практическое действие из выбранного источника.",
                             instruction, "Записано, что сделано и какой результат удалось получить.", source,
                             english=(title, "This action comes from the selected source.",
                                      instruction, "Record what you did and what happened."))
            steps.append(step)
            grounded_count += 1
    for step in ad_instructions:
        if len(steps) == target - 1 or grounded_count >= 2:
            break
        if not _duplicate(step["instruction"], steps):
            steps.append({**step, "source": source})
            grounded_count += 1
    if len(steps) == 1:
        return {**result, "clarification": "Какую часть задачи вы можете выполнить на этой неделе?",
                "clarification_en": "Which part of the task can you complete this week?"}
    steps.append(_step("Проверьте результат", "Сравнение покажет, что удалось выполнить и где нужна помощь.",
                       "Сопоставьте результат с целью недели. Отметьте, что получилось, и одно препятствие, если оно осталось.",
                       "Есть отметка о выполнении и следующий вопрос, если результат пока неполный.", steps[-1]["source"],
                       english=("Check your result", "This shows where you made progress and where you still need help.",
                                "Compare your result with the weekly goal. Record what worked and one remaining obstacle.",
                                "You have noted what was completed and your next question, if any.")))
    result.update(steps=_finish_steps(steps, available, per_day),
                  incomplete=grounded_count < 2 and available >= 40)
    return result


def next_material(catalog, question: str, profile: dict, *, step=None, outcome: str = "success",
                  note: str = "", used=(), learning_intent: bool = False) -> dict | None:
    """Choose a relevant card from search results with intentional source reuse."""
    if not question:
        return None
    try:
        goal, safe_note = _sanitize(question), _sanitize(note)
    except PrivacyRejected:
        return None
    demo = bool(getattr(catalog, "is_demo", False))
    last_source = (step or {}).get("source") or {}
    used_keys = set(used) | {last_source.get("url"), last_source.get("id")}
    focus = " ".join((goal, safe_note))
    template = bool(_TEMPLATE.search(focus)) and not _NO_TEMPLATE.search(focus)
    note_learning = bool(_LEARNING.search(safe_note))
    learning_terms = _action_key(safe_note) - _LEARNING_WORDS if note_learning else set()
    paid_allowed = not _FREE_ONLY.search(focus) and (learning_intent or note_learning or bool(_LEARNING.search(goal)))

    # Extra note-based lookup is scoped to the original topic. Avoid replacing a
    # narrower question (e.g. a named platform) with an unrelated recommendation.
    searches = [_search(catalog, goal, profile)]
    if safe_note and (template or len(_action_key(safe_note) & _action_key(goal)) >= 2):
        searches.insert(0, _search(catalog, safe_note, profile))

    ordered = []
    seen = set()
    for search in searches:
        evidence_ids = {(item.url, item.kind, item.title): item.material_id for item in search.evidence
                        if item.material_id and item.content_scope in _PUBLIC_CARD}
        for item in (*search.recommendations, *search.evidence):
            card = _record(item, demo=demo)
            if not card:
                continue
            card["id"] = evidence_ids.get((item.url, item.kind, item.title), card["id"])
            if profile.get("stage") == "launch" and re.search(r"действующ\w+\s+(?:бизнес|кофейн|магазин)", card["title"], re.I):
                continue
            key = card["url"] or card["id"]
            if key in seen or (template and card["kind"] != "document"):
                continue
            if card["paid"] and (not paid_allowed or card["kind"] != "course"):
                continue
            if card["kind"] == "course" and learning_terms and not learning_terms & _action_key(
                    f"{card['title']} {card['description']}"):
                continue
            seen.add(key)
            ordered.append(card)

    if paid_allowed and not template:
        ordered.sort(key=lambda card: not card["paid"] and card["kind"] != "course")
    for card in ordered:
        if not {card["id"], card["url"]} & (used_keys - {"", None}):
            if card["paid"]:
                ru, en = ("Вы запросили более системное обучение по этой теме.",
                          "You requested more structured training on this topic.")
            elif template:
                ru, en = ("Вы запросили рабочий шаблон по этой задаче.",
                          "You requested a working template for this task.")
            elif outcome == "success":
                ru, en = ("Материал помогает продолжить работу по исходной задаче.",
                          "This material helps you continue working on the original task.")
            else:
                ru, en = ("Предыдущее действие ещё не доведено до результата. Материал поможет выбрать доработку.",
                          "The previous step still needs work. This material can help you choose an adjustment.")
            return {**card, "reason": ru, "reason_en": en, "repeated": False}

    if outcome != "success" and safe_note and last_source:
        for card in ordered:
            if not card["paid"] and {card["id"], card["url"]} & {last_source.get("id"), last_source.get("url")} - {"", None}:
                return {**card, "reason": "Вернитесь к материалу текущего действия и проверьте, на каком этапе возникло препятствие.",
                        "reason_en": "Return to the material for the current step and identify where you got stuck.",
                        "repeated": True}
    if outcome == "success" and last_source.get("id") and last_source.get("paid") is False:
        if {last_source["id"], last_source.get("url")} & (set(used) - {"", None}):
            return None
        stored = SimpleNamespace(
            title=last_source.get("title", ""), url=last_source.get("url", ""),
            kind=last_source.get("kind"), material_id=last_source["id"],
            description=last_source.get("description", ""), price_display=last_source.get("price_display", ""),
        )
        card = _record(stored, demo=demo)
        if card and (not template or card["kind"] == "document"):
            return {**card, "reason": "Этот источник пригодится для следующего действия плана.",
                    "reason_en": "This source supports the next step in your plan.", "repeated": False}
    return None
