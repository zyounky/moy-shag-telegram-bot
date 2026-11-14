"""Small offline vocabulary for searching a Russian catalogue from English."""

import re


_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_CONCEPTS = (
    (r"test demand|check demand|validate demand", "проверить спрос"),
    (r"before (?:opening|launching)", "до открытия"),
    (r"(?:planning|preparing) to open", "планирую открыть"),
    (r"already (?:open|operating|running)", "уже работает"),
    (r"coffee[- ]shops?|caf[eé]s?", "кофейня"),
    (r"(?:advertising|ad) performance|advertising effectiveness", "эффективность рекламы"),
    (r"advertising|ads?|marketing", "реклама"),
    (r"bookings?|reservations?", "брони"),
    (r"orders?", "заказы"),
    (r"demand", "спрос"),
    (r"reconcile", "сверить"),
    (r"ozon", "маркетплейса Ozon"),
    (r"marketplaces?", "маркетплейс"),
    (r"sales", "продажи"),
    (r"reports?", "отчёт"),
    (r"cash\s+flow", "денежный поток"),
    (r"business\s+plan", "бизнес-план"),
    (r"tax(?:es)?", "налог"),
)
_QUERY_WORDS = (
    (r"what (?:is|are)", "что такое"),
    (r"how", "как"),
    (r"measure", "измерить"),
    (r"(?:i )?(?:want|would like) to", "хочу"),
)
_FILLER = (
    r"a|an|the|i|we|my|our|its|can|could|do|does|did|should|please|"
    r"am|is|are|for|with|of|to|in|from"
)


def russian_search_query(text: str) -> str:
    """Normalize covered English terms and preserve unfamiliar topic words.

    This is search normalization rather than translation. It covers a small
    business vocabulary, and strings already containing Russian stay intact.
    """
    if _CYRILLIC.search(text):
        return text
    converted = text
    for term, replacement in _CONCEPTS:
        converted = re.sub(rf"\b(?:{term})\b", replacement, converted, flags=re.IGNORECASE)
    if converted == text:
        return text
    for term, replacement in _QUERY_WORDS:
        converted = re.sub(rf"\b(?:{term})\b", replacement, converted, flags=re.IGNORECASE)
    converted = re.sub(rf"\b(?:{_FILLER})\b", " ", converted, flags=re.IGNORECASE)
    return re.sub(r"\s+([,?!])", r"\1", " ".join(converted.split()))
