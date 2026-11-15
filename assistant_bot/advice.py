import re
from dataclasses import dataclass

from .answer_policy import (ad_finance_intent, break_even_advice, general_staff_kpi,
                            missing_leads, sales_staff_title, shop_example_mismatch, unsupported_claim)


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
    "operate": (r"уже работа|работаем|работает|открылись|есть действующ"
                r"|(?:у меня|у нас) (?:уже )?(?:работающ|действующ)\w+"),
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
_AD_VISITS = re.compile(
    r"приводит ли (?:она|реклама|кампания) (?:новых )?(?:гостей|посетителей)", re.I
)


def infer_profile(text: str, profile: dict):
    normalized = text.lower()
    stages = {
        stage for stage, pattern in STAGE_PATTERNS.items()
        for match in re.finditer(pattern, normalized)
        if not re.search(r"\bне\s+$", normalized[max(0, match.start() - 5):match.start()])
    }
    if len(stages) == 1:
        profile["stage"] = stages.pop()
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
    if _ADVERTISING.search(query) and _AD_VISITS.search(query):
        return AD_GOALS["визит"]
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
    answer: str = ""
    sources: tuple = ()
    local_complete: bool = False
    clarification: str = ""


def compose_advice(query: str, evidence, profile: dict, obstacle: str = "") -> Advice:
    # Only the retrieval result can supply substantive facts and source URLs.
    passage = _passage(query, evidence.passage)
    stage = profile.get("stage")
    stage_context = STAGES.get(stage, "")
    sector = profile.get("sector")
    context = " · ".join(value for value in (sector, stage_context) if value)
    regulated = bool(_REGULATED.search(query))
    completion = ""
    sourced = ((evidence.title, evidence.url),)

    if calculation := break_even_advice(query, evidence):
        answer, action, completion = calculation
        return Advice(passage, evidence.title, evidence.url, action, context, regulated,
                      completion, answer, sourced, local_complete=True)
    if getattr(evidence, "content_scope", "public_text") in {"description", "document_description"}:
        answer = ("В каталоге доступно описание материала. По нему можно понять назначение, "
                  "а содержание файла, видео или инструмента нужно проверить на странице материала.")
        action = "Откройте карточку материала и проверьте, даёт ли она доступ к нужному формату."
        completion = "Отмечено, есть ли в карточке нужный файл, видео или инструмент."
        return Advice(passage, evidence.title, evidence.url, action, context, regulated,
                      completion, answer, sourced)
    if not regulated and ad_finance_intent(query) and missing_leads(f"{query}\n{obstacle}"):
        answer = ("Без числа заявок нельзя проверить стоимость заявки. Для оценки окупаемости "
                  "проверьте отдельно, сохранились ли расходы на рекламу и данные о продажах, "
                  "выручке или прибыли, которые можно связать с кампанией за тот же период.")
        action = "Заполните для одной кампании строки: период, расходы и связанные с ней финансовые данные. Отметьте пропуски."
        completion = "Для каждой строки записано значение с источником данных или указано, что данных нет."
        return Advice(passage, evidence.title, evidence.url, action, context, regulated,
                      completion, answer, sourced)
    if general_staff_kpi(query):
        answer = ("Выберите показатели отдельно для каждой роли. Проверьте, влияет ли сотрудник "
                  "на показатель и можно ли его измерить одинаково для людей с той же ролью. "
                  "Числовые пороги премирования отдела продаж нельзя автоматически переносить на другие роли.")
        action = "Выберите одну роль в команде и запишите её задачу, показатель и способ его измерения."
        completion = "Для одной роли записаны задача, один показатель и источник данных для него."
        supported_example = (evidence.content_scope == "public_text" and sales_staff_title(evidence.title)
                             and re.search(r"80\s*%?\s*[-–—]\s*85\s*%", evidence.passage)
                             and re.search(r"80\s*%?\s*[-–—]\s*85\s*%", query))
        if supported_example:
            answer = ("Нет: порог премии из примера для менеджеров по продажам нельзя автоматически "
                      "переносить на всех сотрудников магазина. Для каждой роли проверьте, влияет ли "
                      "человек на показатель и как его измерять. Сам пример не подтверждает подходящий "
                      "порог для другой роли.")
        return Advice(passage, evidence.title, evidence.url, action, context, regulated,
                      completion, answer, sourced, local_complete=bool(supported_example))
    if shop_example_mismatch(query, evidence.title):
        answer = ("Источник описывает магазин одежды. Это пример для конкретного ассортимента. "
                  "Уточните, что продаёт ваш магазин, чтобы проверить применимость совета.")
        action = "Укажите основную категорию товаров и задачу, которую хотите решить."
        completion = "Названы категория товаров и одна задача магазина."
        assortment_stated = re.search(
            r"магазин\w*\s+(?!(?:без|с|у|в|на|для|и|уже|пока|как|хочу|бюджет|работает)\b)[а-яёa-z]+",
            query, re.I)
        return Advice(passage, evidence.title, evidence.url, action, context, regulated,
                      completion, answer, sourced,
                      clarification=("Что продаёт ваш магазин? Это поможет подобрать материалы по вашему ассортименту."
                                     if not assortment_stated else ""))

    tokens = _words(query)
    candidates = [
        sentence for sentence in _sentences(evidence.passage)
        if _ACTION.match(sentence) and not sentence.endswith("?")
        and tokens & _words(sentence) and 35 <= len(sentence) <= 180
        and not unsupported_claim(query, obstacle, sentence)
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
        action = actions.get(stage) or "Выберите одну кампанию и один показатель из материала."
        completion = {
            "launch": "Выбран показатель для первого рекламного теста.",
            "operate": "Записаны цель кампании и фактический результат по тому же показателю.",
            "grow": "Записаны результаты двух кампаний по одному показателю за одинаковый период.",
        }.get(stage, "Записаны название кампании и показатель для проверки.")
        if goal:
            action = (f"До запуска выберите, где будете считать {goal[0]} от рекламы." if stage == "launch"
                      else f"Выберите одну кампанию и посчитайте {goal[0]}, которые она принесла за выбранный период.")
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
