"""Local redaction and fail-closed validation for external model inputs."""

import json
import re
import unicodedata


class PrivacyRejected(ValueError):
    """A field could not be prepared safely for external processing."""


_PROFILE_KEYS = ("stage", "sector", "goal")
_PUBLIC_SCOPES = {"public_text", "free_lesson", "description", "document_description"}
_LIMITS = {"question": 750, "obstacle": 400, "title": 180, "text": 3600}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I)
_EMAIL_OBFUSCATED = re.compile(
    r"[\w.+-]+\s*(?:[(\[]\s*(?:at|собака)\s*[)\]]|＠)\s*[\w-]+"
    r"(?:\s*(?:\.|[(\[]\s*(?:dot|точка)\s*[)\]])\s*[\w-]+)+", re.I,
)
_URL = re.compile(r"(?:https?://|tg://|www\.|t\.me/)[^\s<>()]+", re.I)
_HANDLE = re.compile(r"(?<![\w@])@[a-z0-9_]{4,32}(?!\w)", re.I)
_SECRET = re.compile(
    r"\b(?:xai-|sk-|sk_live_|sk_test_|gh[pousr]_|github_pat_|AIza|AKIA)[a-z0-9_-]{8,}\b"
    r"|\b\d{5,12}:[a-z0-9_-]{16,}\b"
    r"|\beyJ[a-z0-9_-]{5,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\b"
    r"|\bBearer[ \t]+[a-z0-9_.~+/=-]{12,}\b", re.I,
)
_EXTENSION = r"(?:[ \t]+(?:доб\.?|ext\.?)[ \t]*[0-9]{1,6})?"
_PHONE = re.compile(
    r"(?<![\w+\d])(?:"
    r"\+[1-9](?:[ \t().-]*[0-9]){9,14}"
    r"|[78][ \t.-]*(?:\([0-9]{3}\)|[0-9]{3})[ \t.-]*[0-9]{3}[ \t.-]*[0-9]{2}[ \t.-]*[0-9]{2}"
    r"|\([0-9]{3}\)[ \t.-]*[0-9]{3}[ \t.-]*[0-9]{4}"
    r"|9[0-9]{9}"
    r"|[0-9]{3}[.-][0-9]{3}[.-][0-9]{4}"
    rf"){_EXTENSION}(?!\w)", re.I,
)
_BUSINESS_UNIT = re.compile(
    r"[ \t]*(?:[₽$€%]|руб(?:\.|\b|л[а-я]*)|(?:USD|EUR|RUB)\b|заказ[а-я]*\b|"
    r"визит[а-я]*\b|единиц[а-я]*\b|штук[а-я]*\b|клиент[а-я]*\b)", re.I,
)
_BUSINESS_METRIC = re.compile(
    r"\b(?:оборот|выручка|прибыль|сумма|расходы|затраты|заказы|визиты)"
    r"[ \t]*[:=]?[ \t]*$", re.I,
)
_FORMATTED_DOC = re.compile(r"(?<!\w)[0-9]{3}-[0-9]{3}-[0-9]{3}[ \t]+[0-9]{2}(?!\w)")
_CARD = re.compile(r"(?<!\w)(?:[0-9]{16}|[0-9]{4}(?:[ -][0-9]{4}){3})(?!\w)")

_DOC_LABEL = (
    r"СНИЛС|ИНН(?:[ \t]+физического[ \t]+лица)?|ОГРНИП|"
    r"(?:серия(?:[ \t]+и[ \t]+номер)?|номер|данные)[ \t]+паспорта|паспорт(?:а)?|"
    r"(?:номер[ \t]+)?(?:банковск(?:ой|ая)[ \t]+)?карт(?:ы|а)|"
    r"(?:номер[ \t]+)?(?:расч[её]тн(?:ого|ый)|лицев(?:ого|ой))[ \t]+сч[её]т(?:а)?|"
    r"(?:номер[ \t]+)?(?:водительского[ \t]+удостоверения|полиса[ \t]+ОМС)|"
    r"social[ \t]+security[ \t]+number|SSN|passport(?:[ \t]+number)?|"
    r"(?:credit[ \t]+)?card[ \t]+number|driver'?s[ \t]+licen[cs]e(?:[ \t]+number)?"
)
_ID_LABEL = (
    r"(?:chat|user|client|customer|account)[_ \t-]?id|taxpayer[ \t]+id|"
    r"(?:id|айди|идентификатор)[ \t]+(?:клиента|пользователя|сотрудника|чата|аккаунта|телеграма)|"
    r"(?:telegram|телеграм)[_ \t-]?(?:id|айди)"
)
_PHONE_LABEL = r"(?:номер[ \t]+)?телефон(?:а)?|тел\.?|мобильный|phone(?:[ \t]+number)?|mobile(?:[ \t]+number)?"
_EMAIL_LABEL = r"email|e-mail|(?:электронная[ \t]+)?почта"
_SECRET_LABEL = (
    r"(?:xai|openrouter|openai)[_ \t-]+api[_ \t-]+key|"
    r"(?:api|xai|openrouter|openai|telegram|bot)[_ \t-]+(?:key|token)|"
    r"(?:ключ|токен)[ \t]+(?:api|бота|telegram)|секретный[ \t]+ключ|"
    r"(?:клиентский[ \t]+)?секрет|client[_ \t-]+secret|access[_ \t-]+token|"
    r"token|токен|password|пароль"
)
_DOB_LABEL = r"дата[ \t]+рождения|date[ \t]+of[ \t]+birth|DOB"
_NUMBER = r"\+?[0-9](?:[0-9]|[ \t().#№-]+(?=[0-9])){2,32}[0-9]"
_ID_VALUE = r"[a-zа-яё0-9][a-zа-яё0-9_.-]{3,63}"
_DELIMITED = r"[ \t]*(?:[:=№#—]|[ \t]+-[ \t]+)[ \t]*"


def _labelled(label, value, *, separator=_DELIMITED + "|[ \t]+"):
    return re.compile(
        rf"(?P<label>\b(?:{label})(?!\w)(?:{separator}))[\"«“']?"
        rf"(?P<value>{value})(?![\w-])[\"»”']?", re.I,
    )


_LABELLED = (
    (_labelled(_SECRET_LABEL, r"[a-zа-яё0-9_][a-zа-яё0-9_./+~=:@-]{3,255}", separator=_DELIMITED),
     "[секрет скрыт]"),
    (_labelled(_DOC_LABEL, rf"(?:[a-zа-я]{{1,3}}[- \t№#]*)?{_NUMBER}"), "[номер документа скрыт]"),
    (_labelled(_ID_LABEL, rf"(?:{_NUMBER}|{_ID_VALUE})"), "[идентификатор скрыт]"),
    (_labelled(_PHONE_LABEL, _NUMBER + _EXTENSION), "[номер скрыт]"),
    (_labelled(_DOB_LABEL, r"[0-9]{1,4}[-./][0-9]{1,2}[-./][0-9]{1,4}"), "[дата рождения скрыта]"),
)
_MARKERS = (
    "[секрет скрыт]", "[номер документа скрыт]", "[идентификатор скрыт]",
    "[номер скрыт]", "[дата рождения скрыта]", "[email скрыт]", "[адрес ссылки скрыт]",
    "[аккаунт скрыт]",
)
_MARKER_PATTERN = "|".join(map(re.escape, _MARKERS))
_ALL_LABELS = "|".join((_DOC_LABEL, _ID_LABEL, _PHONE_LABEL, _EMAIL_LABEL, _SECRET_LABEL, _DOB_LABEL))
# Unrecognized values after explicit field labels may be unusual formatting or free text.
_UNCLEARED = re.compile(
    rf"\b(?:{_ALL_LABELS})(?!\w){_DELIMITED}(?![ \t]|{_MARKER_PATTERN})(?=\S)", re.I,
)
_JUST_DISCLOSURE = re.compile(
    rf"[ \t]*(?:(?:мой|моя|my)[ \t]+)?(?:{_ALL_LABELS})(?:{_DELIMITED}|[ \t]+)"
    rf"(?:{_MARKER_PATTERN})[ \t.!?]*", re.I,
)
_PERSONAL = re.compile(
    r"\b(?:(?:меня[ \t]+зовут|зовут[ \t]+меня)|мо[её][ \t]+(?:имя|ФИО)|my[ \t]+name[ \t]+is)[ \t]+[а-яa-zё]+"
    r"|\b(?:ФИО|Ф\.И\.О\.?|имя(?:[ \t]+и[ \t]+фамилия)?|фамилия|full[ \t]+name)"
    r"(?:[ \t]+(?:клиента|сотрудника|пользователя))?[ \t]*[:=—][ \t]*\S"
    r"|\b(?:мой[ \t]+адрес|адрес[ \t]+(?:проживания|клиента)|my[ \t]+address(?:[ \t]+is)?)"
    r"(?:[ \t]*[:=—][ \t]*|[ \t]+)\S"
    r"|\b(?:адрес|address)[ \t]*[:=—][ \t]*(?!(?:https?://|www\.|t\.me/|\[адрес ссылки скрыт\]))\S"
    r"|\b(?:живу|проживаю)[ \t]+(?:в[ \t]+)?(?:г\.|городе|ул\.|улице|проспекте|доме)[ \t]+[а-яёa-z]",
    re.I,
)
_PERSON_CONTEXT = re.compile(
    r"\b(?:[Яя]|[Кк]лиент[ау]?|[Сс]отрудник[ау]?|ФИО)\s*[:—-]?\s+"
    r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\b"
)
_STREET = re.compile(
    r"\b(?:ул\.|улица|улице|проспект|пр-т|переулок|шоссе|бульвар)[ \t]+[а-яёa-z .-]{1,60}[, \t]+"
    r"(?:д(?:ом)?\.?[ \t]*)?[0-9]+", re.I,
)
_STREET_EN = re.compile(
    r"\b[0-9]{1,5}[ \t]+[a-z][a-z .'-]{1,55}[ \t]+"
    r"(?:street|st\.|road|rd\.|avenue|ave\.|boulevard|lane|drive)\b", re.I,
)


def _normalized(value):
    if not isinstance(value, str):
        raise PrivacyRejected("Unexpected outbound field type")
    clean = []
    for char in unicodedata.normalize("NFKC", value):
        category = unicodedata.category(char)
        if category in {"Cs", "Cc"} and char not in "\r\n\t":
            raise PrivacyRejected("Invalid outbound character")
        if category != "Cf":
            clean.append(char)
    return "".join(clean)


def has_personal_disclosure(text):
    """Tell the Telegram flow to reject input that cannot safely be redacted."""
    try:
        _sanitize(text)
    except PrivacyRejected:
        return True
    return False


def _is_unstructured(value):
    return bool(_PERSONAL.search(value) or _PERSON_CONTEXT.search(value)
                or _STREET.search(value) or _STREET_EN.search(value))


def _is_business_quantity(match):
    # A currency sign or explicit business unit makes an unlabelled number a quantity.
    return bool(_BUSINESS_UNIT.match(match.string, match.end())
                or _BUSINESS_METRIC.search(match.string[max(0, match.start() - 24):match.start()]))


def _mask_phone(match):
    return match[0] if _is_business_quantity(match) else "[номер скрыт]"


def _mask_card(match):
    if _is_business_quantity(match):
        return match[0]
    digits = [int(char) for char in reversed(match[0]) if char.isascii() and char.isdigit()]
    checksum = sum(digits[::2]) + sum(digit * 2 - (9 if digit >= 5 else 0) for digit in digits[1::2])
    return "[номер документа скрыт]" if checksum % 10 == 0 else match[0]


def _sanitize(value):
    text = _normalized(value)
    if _is_unstructured(text) or re.search(r"-----BEGIN .*PRIVATE KEY-----", text, re.I):
        raise PrivacyRejected("Unclear personal disclosure")
    for label, marker in _LABELLED:
        text = label.sub(lambda match: match["label"] + marker, text)
    text = _URL.sub("[адрес ссылки скрыт]", text)
    text = _EMAIL.sub("[email скрыт]", _EMAIL_OBFUSCATED.sub("[email скрыт]", text))
    text = _SECRET.sub("[секрет скрыт]", text)
    text = _FORMATTED_DOC.sub("[номер документа скрыт]", text)
    text = _CARD.sub(_mask_card, text)
    text = _PHONE.sub(_mask_phone, text)
    text = _HANDLE.sub("[аккаунт скрыт]", text)
    if _UNCLEARED.search(text) or _JUST_DISCLOSURE.fullmatch(text):
        raise PrivacyRejected("Unclear or standalone personal field")
    return text


def _prepare(value, limit):
    clean = _sanitize(value)
    if len(clean) > limit:
        clean = clean[:limit]
        # Do not emit half a replacement marker after applying the length limit.
        start = clean.rfind("[")
        if start > clean.rfind("]") and any(marker.startswith(clean[start:]) for marker in _MARKERS):
            clean = clean[:start]
    return clean


def prepare_outbound(question, profile, obstacle, evidence):
    """Filter full input before applying the existing public API field limits."""
    return {
        "question": _prepare(question, _LIMITS["question"]),
        "context": {key: _prepare(profile[key], 100) for key in _PROFILE_KEYS
                    if isinstance(profile.get(key), str)},
        "obstacle": _prepare(obstacle, _LIMITS["obstacle"]),
        "passages": [
            {"source_index": index, "title": _prepare(source.title, _LIMITS["title"]),
             "kind": _prepare(source.kind, 40), "content_scope": _prepare(source.content_scope, 40),
             "text": _prepare(source.passage, _LIMITS["text"])}
            for index, source in enumerate(evidence)
        ],
    }


def validate_outbound(payload):
    """Validate the dynamic content of the final API body, including schema quotes."""
    inputs = json.loads(payload["input"][1]["content"])
    if set(inputs) != {"question", "context", "obstacle", "passages"}:
        raise PrivacyRejected("Unexpected outbound fields")
    context = inputs["context"]
    passages = inputs["passages"]
    if not isinstance(context, dict) or not set(context) <= set(_PROFILE_KEYS) \
            or not isinstance(passages, list) or not 1 <= len(passages) <= 3:
        raise PrivacyRejected("Unexpected outbound structure")
    model = payload["model"]
    values = [inputs["question"], inputs["obstacle"], model, *context.values()]
    quote_values = []
    for index, passage in enumerate(passages):
        if (set(passage) - {"quote_options"} != {"source_index", "title", "kind", "content_scope", "text"}
                or type(passage["source_index"]) is not int or passage["source_index"] != index
                or passage["content_scope"] not in _PUBLIC_SCOPES):
            raise PrivacyRejected("Unexpected outbound source structure")
        values.extend(passage[key] for key in ("title", "kind", "content_scope", "text"))
        options = passage.get("quote_options", [])
        if not isinstance(options, list) or any(not isinstance(quote, str) or quote not in passage["text"]
                                                for quote in options):
            raise PrivacyRejected("Unexpected outbound quote options")
        quote_values.extend(options)
    quote_enum = payload["text"]["format"]["schema"]["properties"]["quote"].get("enum")
    if quote_enum is not None and quote_enum != ["", *dict.fromkeys(quote_values)]:
        raise PrivacyRejected("Unexpected outbound quote schema")
    if any(value != _sanitize(value) for value in [*values, *quote_values]):
        raise PrivacyRejected("Residual sensitive field")
