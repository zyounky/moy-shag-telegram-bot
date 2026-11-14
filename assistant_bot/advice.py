import re
from dataclasses import dataclass


STAGES = {
    "launch": "готовится к запуску",
    "operate": "уже работает",
    "grow": "развивается",
}

SECTORS = {
    "кофейн": "Кофейня",
    "кафе": "Кафе",
    "ресторан": "Общепит",
    "магазин": "Розница",
    "маркетплейс": "Маркетплейсы",
    "салон": "Услуги",
    "услуг": "Услуги",
    "студи": "Студия",
}

STAGE_PATTERNS = {
    "launch": r"планирую открыть|собираюсь открыть|готовлюсь к открытию|до открытия|ещ[её] не откры",
    "grow": r"расширяемся|масштабир|вторая точка|вторую точку|выходим в новый",
    "operate": r"уже работа|работаем|работает|открылись|есть действующ",
}

AD_GOALS = {
    "заказ": ("заказы", "число заказов"),
    "звон": ("звонки", "число звонков"),
    "брон": ("брони", "число броней"),
    "заяв": ("заявки", "число заявок"),
    "визит": ("визиты", "число визитов"),
}

_ACTION = re.compile(
    r"\b(?:определ|выбер|состав|сравн|проверь|проверя|посчита|рассчита|"
    r"попроб|оцен|укаж|выпиш|раздел|добав|изуч|настрой|созда|заполн|"
    r"начн|использ|установ|собер|зафикс|подготов|возьм|запиш)\w*", re.I
)
_WORDS = re.compile(r"[а-яёa-z0-9]{4,}", re.I)
_STOP = frozenset("что как какие чтобы можно нужно мне меня моей своих ваших вашего хочу бизнес бизнеса".split())
_REGULATED = re.compile(r"налог|\bусн\b|\bндс\b|штраф|деклараци|закон|пошлин", re.I)
_ADVERTISING = re.compile(r"реклам|кампани|объявлен", re.I)
_NO_AD_MEASUREMENT = re.compile(
    r"(?:нет|не вид|не знаю|не сохраня|не счит).{0,45}(?:касс|данн|промокод|статистик|уч[её]т)"
    r"|(?:данн|промокод|статистик).{0,30}(?:нет|не сохран|недоступ)", re.I
)


def infer_profile(text: str, profile: dict):
    normalized = text.lower()
    for stage, pattern in STAGE_PATTERNS.items():
        if re.search(pattern, normalized):
            profile["stage"] = stage
            break
    for prefix, label in SECTORS.items():
        if re.search(r"\b" + prefix, normalized):
            profile["sector"] = label
            break
    if goal := _ad_goal(text):
        profile["goal"] = goal[1]


def _ad_goal(query: str):
    match = re.search(
        r"(?:хочу|цель|отслежива\w*|измеря\w*|счита\w*)\b[^.!?]{0,45}"
        r"\b(заказ\w*|звон\w*|брон\w*|заяв\w*|визит\w*)",
        query.casefold(),
    )
    if match:
        return next(value for prefix, value in AD_GOALS.items() if match[1].startswith(prefix))
    return None


def catalog_context(profile: dict) -> dict:
    return {
        "stage": STAGES.get(profile.get("stage", ""), ""),
        "sector": profile.get("sector", ""),
        "goal": profile.get("goal", ""),
    }


def _words(text: str) -> set[str]:
    return {word[:5] for word in _WORDS.findall(text.lower().replace("ё", "е")) if word not in _STOP}


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _sentences(text: str) -> list[str]:
    lines = re.split(r"(?<=[.!?])\s+|\n+|\s+[•●]\s+", text)
    return [line.strip(" \t—–•") for line in lines if len(line.strip()) >= 28]


def _passage(query: str, text: str) -> str:
    candidates = _sentences(text)
    tokens = _words(query)
    if not candidates:
        return _shorten(" ".join(text.split()), 345)
    best = max(range(len(candidates)), key=lambda i: len(tokens & _words(candidates[i])))
    snippet = candidates[best]
    if len(snippet) < 120 and best + 1 < len(candidates):
        snippet += " " + candidates[best + 1]
    return _shorten(snippet, 345)


@dataclass(frozen=True)
class Advice:
    passage: str
    title: str
    url: str
    action: str
    context: str
    regulated: bool
    completion: str = ""


def compose_advice(query: str, evidence, profile: dict, obstacle: str = "") -> Advice:
    # Only the retrieval result can supply substantive facts and source URLs.
    passage = _passage(query, evidence.passage)
    stage = profile.get("stage")
    stage_context = STAGES.get(stage, "")
    sector = profile.get("sector")
    context = " · ".join(value for value in (sector, stage_context) if value)
    regulated = bool(_REGULATED.search(query))
    completion = ""

    tokens = _words(query)
    candidates = [
        sentence for sentence in _sentences(evidence.passage)
        if _ACTION.match(sentence) and not sentence.endswith("?")
        and tokens & _words(sentence) and 35 <= len(sentence) <= 180
    ]
    practical = max(candidates, key=lambda sentence: len(tokens & _words(sentence)), default="")
    if regulated:
        action = "Сверить актуальные условия для своего режима и периода перед действием."
    elif _ADVERTISING.search(query) and _ADVERTISING.search(evidence.title):
        goal = _ad_goal(query)
        actions = {
            "launch": "До запуска выберите одно целевое действие для первого рекламного теста.",
            "operate": "Возьмите одну действующую кампанию и сравните её цель с фактическим результатом.",
            "grow": "Выберите один показатель и сравните его для двух кампаний за одинаковый период.",
        }
        action = actions.get(stage) or practical or "Выберите показатель из материала и проверьте одну кампанию."
        completion = "Записана цель и понятно, где взять фактический результат."
        if goal:
            action = (f"До запуска выберите, где будете считать {goal[0]} от рекламы." if stage == "launch"
                      else f"Выберите одну кампанию и посчитайте {goal[0]} за период её работы.")
            completion = (f"Выбрано, где фиксировать {goal[0]}, и записан период теста." if stage == "launch"
                          else f"Записано {goal[1]} за выбранный период и указан источник данных.")
        if _NO_AD_MEASUREMENT.search(obstacle):
            action = ("Отметьте, какие результаты доступны в рекламном кабинете. "
                      "Выберите один доступный показатель для следующего теста.")
            completion = "Выбраны показатель и место, где вы сможете его увидеть."
    elif stage == "launch":
        action = "Открыть материал и выписать одну проверку, которую можно сделать до запуска."
        if practical:
            action = "До запуска: " + _shorten(practical, 145)
    elif stage == "operate":
        action = "Выбрать из материала одно изменение и проверить его на текущей работе."
        if practical:
            action = "На текущей задаче: " + _shorten(practical, 140)
    elif stage == "grow":
        action = "Выбрать один процесс для проверки и заранее записать, по чему оцените результат."
    else:
        action = _shorten(practical, 150) if practical else (
            "Открыть материал и выбрать один шаг, который можно применить сегодня."
        )

    return Advice(passage, evidence.title, evidence.url, action, context, regulated, completion)
