"""Local checks for recurring errors in business answers."""

from decimal import Decimal, ROUND_HALF_UP, localcontext
import re


_AMOUNT = r"(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d{1,12})(?:[,.]\d{1,4})?"
_SIGNED = rf"[-−+]?{_AMOUNT}"
_SEPARATOR = r"(?:[:=]|[—–-](?=\s))?"
_BREAK_EVEN = re.compile(
    r"\b(?:точк\w*|выручк\w*\s+(?:для\s+|при\s+)?)\s*безубыточност\w*|\bbreak[ -]?even", re.I
)
_FIXED_COST = re.compile(
    rf"\bпостоянн\w+\s+(?:затрат\w*|расход\w*|издерж\w*)\s*"
    rf"(?:составля\w*\s*)?{_SEPARATOR}\s*({_SIGNED})\s*(?:₽|руб(?:л\w*)?\.?)(?!\w)", re.I
)
_MARGIN_VALUE = re.compile(
    rf"\b(?:(доля|долю)\s+марж\w*|маржинальност\w*|маржа)\s*"
    rf"{_SEPARATOR}\s*({_SIGNED})(?!\d|[,.]\d)\s*(%|процент\w*)?", re.I
)
_ALT_PERCENT = re.compile(rf"^\s*[,;(]?\s*(?:то есть\s+)?({_SIGNED})\s*%", re.I)
_BREAK_EVEN_REVENUE_SOURCE = re.compile(
    r"постоянн\w*\s+(?:расход\w*|затрат\w*|издерж\w*).{0,120}"
    r"(?:/|÷|на\s+(?:дол|марж)|дел[ияён]).{0,75}(?:марж|%(?:\s|$))"
    r"|раздел\w*.{0,70}постоянн\w*\s+(?:расход\w*|затрат\w*|издерж\w*)"
    r".{0,80}на\s+(?:дол|марж)", re.I | re.S
)
_AD = re.compile(r"реклам|кампани|продвижен|маркетинг|(?<!\w)ads?(?!\w)", re.I)
_AD_PROFIT = re.compile(r"окуп\w*|рентабельност|\bromi\b|\broi\b|\broas\b|return on", re.I)
_MISSING_LEADS = re.compile(
    r"(?:не (?:записыва|учитыва|отслежива|фиксир|сохраня|счит)\w*|нет (?:уч[её]та|числа|данных по))"
    r".{0,25}(?:заяв\w*|лид\w*)|(?:заяв\w*|лид\w*).{0,20}"
    r"не (?:записыва|учитыва|отслежива|фиксир|сохраня|счит)\w*", re.I
)
_STAFF = re.compile(r"сотрудни|персонал|команд|работник|staff|employees", re.I)
_RETENTION = re.compile(r"удерж\w*|увольня\w*|текуч\w*|retention|\bquit\b", re.I)
_SALES_ROLE = re.compile(r"продавц\w*|менеджер\w+ по продажам|sales (?:staff|manager|team)", re.I)
_KPI = re.compile(r"\b(?:kpi|кпэ)\b|преми|(?:оцен|показател)\w*\s+(?:работы|эффективности)", re.I)
_SHOP = re.compile(r"магазин|розниц|retail|\bshop\b|\bstore\b", re.I)
_CLOTHING = re.compile(r"одежд|\bclothing\b|\bapparel\b", re.I)
_NO_BUDGET = re.compile(
    r"(?<!не )\b(?:нет|без)\s+(?:рекламного\s+)?бюджет\w*"
    r"|\bбюджет\w*\s*[:=—–-]?\s*(?:0(?![\d,.])|ноль|отсутствует)\b"
    r"|\b(?:no|zero) (?:ad |advertising )?budget\b", re.I
)
_UNKNOWN_BUDGET = re.compile(
    r"(?:не зна\w*|неизвест\w*|не определ\w*).{0,20}бюджет|бюджет.{0,20}"
    r"(?:неизвест\w*|не определ\w*|не знаю)|budget.{0,16}(?:unknown|undecided)", re.I
)
_TAX_PROCEDURE = re.compile(
    r"(?:как|чем|сколько|когда|нужно ли).{0,45}(?:плати|платить|рассчит|счита|оплати|деклар)"
    r".{0,45}(?:налог|взнос)|(?:какие|какой|какова|срок\w*|ставк\w*).{0,25}налог", re.I | re.S
)
_TAX_REGIME = re.compile(
    r"\b(?:усн|аусн|осн[о]?|нпд|псн|есхн|самозанят\w*|патент\w*|упрощ[её]н\w*)\b", re.I
)


def break_even_intent(question):
    return bool(_BREAK_EVEN.search(question))


def ad_finance_intent(question):
    return bool(_AD.search(question) and _AD_PROFIT.search(question) or re.search(r"\bromi\b|\broas\b", question, re.I))


def missing_leads(question):
    return bool(_MISSING_LEADS.search(question))


def staff_retention_intent(question):
    return bool(_STAFF.search(question) and _RETENTION.search(question))


def general_staff_kpi(question):
    return bool(_STAFF.search(question) and _KPI.search(question) and not _SALES_ROLE.search(question))


def sales_staff_title(title):
    return bool(_SALES_ROLE.search(title))


def shop_example_mismatch(question, title):
    return bool(_SHOP.search(question) and not _CLOTHING.search(question) and _CLOTHING.search(title))


def tax_regime_needed(question):
    return bool(_TAX_PROCEDURE.search(question) and not _TAX_REGIME.search(question))


def budget_state(question):
    if _UNKNOWN_BUDGET.search(question):
        return "unknown"
    if _NO_BUDGET.search(question):
        return "none"
    return "unspecified"


def _decimal(value):
    return Decimal(re.sub(r"\s", "", value).replace(",", ".").replace("−", "-"))


def _display(value):
    whole, _, decimal = f"{value:,f}".partition(".")
    decimal = decimal.rstrip("0")
    whole = whole.replace(",", " ")
    return f"{whole},{decimal}" if decimal else whole


def break_even_advice(question, evidence):
    """Check explicitly stated inputs against a monetary break-even source."""
    if (not break_even_intent(question) or evidence.content_scope != "public_text"
            or not _BREAK_EVEN_REVENUE_SOURCE.search(evidence.passage)):
        return None
    if re.search(r"\bв (?:штуках|единицах)\b|\bсколько (?:штук|единиц)\b", question, re.I):
        return None
    prefix = ("Точка безубыточности в рублях = постоянные затраты ÷ долю маржи. "
              "Если маржинальность дана в процентах, сначала разделите её на 100, "
              "например 40% = 0,4. В обозначении «затраты / маржинальность» "
              "в знаменателе нужна доля.")
    completion = "Записаны постоянные затраты в рублях, период и доля маржи от выручки."
    action = "Запишите сумму постоянных затрат за выбранный период и долю маржи от выручки за этот же период."
    fixed_values = {_decimal(match[1]) for match in _FIXED_COST.finditer(question)}
    fractions = set()
    ambiguous = False
    for match in _MARGIN_VALUE.finditer(question):
        value = _decimal(match[2])
        if alternative := _ALT_PERCENT.match(question[match.end():]):
            fractions.add(_decimal(alternative[1]) / 100)
        if match[3]:
            fractions.add(value / 100)
        elif match[1]:
            fractions.add(value)
        else:
            ambiguous = True
    if len(fixed_values) > 1 or len(fractions) > 1:
        return prefix + "\n\nУкажите одну сумму затрат и согласованную долю маржи для одного периода.", action, completion
    if ambiguous:
        return prefix + "\n\nУточните, что означает указанная маржинальность: долю от 0 до 1 или проценты.", action, completion
    if not fixed_values or not fractions:
        return prefix + "\n\nДля расчёта нужны обе величины за один и тот же период.", action, completion
    fixed, = fixed_values
    fraction, = fractions
    if fixed < 0 or not 0 < fraction <= 1:
        issue = ("Постоянные затраты не могут быть отрицательными в этой формуле."
                 if fixed < 0 else "Для этой формулы нужна положительная доля не больше 1.")
        return prefix + "\n\n" + issue + " Проверьте значения и единицы измерения.", action, completion
    with localcontext() as context:
        context.prec = 36
        result = (fixed / fraction).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    fraction_text = _display(fraction)
    result_text = _display(result)
    answer = (f"Точка безубыточности — {_display(result)} ₽ выручки за тот же период. "
              f"{_display(fixed)} ₽ ÷ {fraction_text} (то есть {_display(fraction * 100)}%) = {result_text} ₽ "
              "после округления до копеек. При такой выручке маржа покрывает постоянные затраты.")
    return (answer, "Сравните плановую выручку за тот же период с вычисленной точкой безубыточности.",
            "Записаны плановая выручка и её отклонение от точки безубыточности, обе суммы в рублях.")


def unsupported_claim(question, obstacle, output):
    """Spot a few concrete invalid inferences, not general factual accuracy."""
    user = f"{question}\n{obstacle}"
    statements = re.split(r"(?<=[.!?])\s+|\n+", output.casefold())
    if general_staff_kpi(user) and re.search(r"\b80\s*%?\s*(?:[-–—−]|до)\s*85\s*%", output):
        return True
    if ad_finance_intent(user) and missing_leads(user) and re.search(
        r"(?:нельзя|невозможно|не получится|нет способа).{0,60}"
        r"(?:romi|roi|roas|окупаемост|прошл\w* (?:эффективност|результат)|восстановить эффективност)"
        r"|(?:romi|roi|roas|окупаемост).{0,28}(?:не восстановить|не рассчитать|невозможно)",
        output, re.I,
    ):
        return True
    if _SHOP.search(user) and not _CLOTHING.search(user) and re.search(
        r"ваш\w* магазин\w* одежды|у вас магазин одежды|для вашего магазина одежды|your clothing store",
        output, re.I,
    ):
        return True
    for statement in statements:
        conditional = re.search(r"\b(?:если|при наличии|при условии|if)\b", statement)
        if budget_state(user) != "none" and not conditional and re.search(
            r"только бесплатн\w* (?:канал|способ)|при нулевом бюджет\w*|у вас (?:нет|нулевой) бюджет"
            r"|zero budget|no budget", statement,
        ):
            return True
        if not conditional and re.search(
            r"(?<!не )бесплатн\w* (?:проверк\w* тайным покупателем|услуг\w* тайного покупателя"
            r"|акци\w*|программ\w* лояльности)"
            r"|(?:акци\w*|программ\w* лояльности|тайный покупатель).{0,18}"
            r"(?:(?<!не )бесплатн|не требу\w* затрат)|(?<!not )free (?:mystery shopper|loyalty program)",
            statement,
        ):
            return True
    return False


def unsupported_scope_claim(scopes, output):
    if not scopes or not all(scope in {"description", "document_description"} for scope in scopes):
        return False
    return bool(re.search(
        r"\b(?:я (?:прочитал|открыл|просмотрел|посчитал)|(?:в файле|в бланке|в шаблоне) "
        r"(?:заполнены|уже есть|есть конкретные|перечислены)|в видео (?:показано|автор объясняет)"
        r"|калькулятор (?:уже )?(?:рассчитал|показал)|I (?:read|watched|opened|calculated))\b",
        output, re.I,
    ))
