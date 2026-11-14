"""Reject obvious personal disclosures before generation; not complete anonymization."""

import re


_DISCLOSURE = re.compile(
    r"\b(?:меня\s+зовут|мо[её]\s+(?:имя|фио)|my\s+name\s+is)\s+\S+"
    r"|\bфио\s*(?:клиента\s*)?[:—-]"
    r"|\b(?:адрес|проживаю|живу)\s*[:—-]?\s*(?:г\.|город|ул\.|улиц[ае]|проспект|дом)"
    r"|\b(?:паспорт|снилс|номер\s+карты|инн\s+физического\s+лица)\D{0,25}\d[\d\s-]{5,}"
    r"|\b(?:my\s+address\s+is|social\s+security\s+number)\s*[:—-]?\s*\S+", re.I)
_PERSON = re.compile(
    r"\b(?:[Яя]|[Кк]лиент[ау]?|[Сс]отрудник[ау]?)\s*[:—-]?\s+"
    r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\b")
_STREET = re.compile(
    r"\b(?:ул\.|улица|проспект|пр-т|переулок)\s+[а-яёa-z .-]{1,60}[, ]+"
    r"(?:д(?:ом)?\.?\s*)?\d", re.I)


def has_personal_disclosure(text):
    return bool(_DISCLOSURE.search(text) or _PERSON.search(text) or _STREET.search(text))
