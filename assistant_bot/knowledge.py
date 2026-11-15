"""Local search over visible articles and educational material cards."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


DEMO_CATALOG = Path(__file__).with_name("demo_catalog.jsonl")
OFFICIAL_HOST = "kurs.alfabank.ru"

STOP_WORDS = set(
    "а без бы быть в вам ваш вас ведь во вот все вы где да для до его ее если есть же за и из или им их к как какая "
    "какие какой ко когда кто куда ли мне мой мы на над надо нас наш не чего него нее нет ни но ну о об один он она "
    "они от по под пока после при про с свое себя со так такое там те тем то того тоже тут ты у уже чем через что "
    "чтобы эта эти это этом этот я можно нужно подскажи расскажи объясни помочь помоги пожалуйста делать сделать "
    "посоветуйте посоветуй начать узнать самом вообще получить хочу планирую собираюсь пожалуйста".split()
)
ENDING = re.compile(
    r"(?:иями|ениями|ями|ами|ение|ения|ению|ыми|ими|ого|его|ому|ему|ать|ить|еть|ую|юю|ая|яя|"
    r"ое|ее|ые|ие|ой|ей|ий|ый|ов|ев|ам|ям|ах|ях|ом|ем|ою|ею|а|я|ь|й|ы|и|о|е|у|ю)$"
)
WORD = re.compile(r"[а-яa-z0-9]+")
INITIALISM = re.compile(r"\b[А-ЯЁA-Z]{2,5}\b")
CONCEPT_WORDS = (
    ("маркетплейс", "маркетплейсы", "селлер"),
    ("ozon", "озон"),
    ("wildberries", "вайлдберриз"),
    ("усн", "упрощенка"),
    ("аусн", "автоусн"),
    ("самозанятость", "самозанятый", "нпд"),
    ("налогообложение", "налог", "налоги", "налоговый"),
    ("реклама", "рекламный", "продвижение", "продвигать", "маркетинг"),
    ("сотрудник", "сотрудники", "персонал", "работник", "команда"),
    ("аренда", "арендовать", "помещение"),
    ("выручка", "доход"),
    ("аудитория", "клиенты", "покупатели"),
    ("регистрация", "зарегистрировать", "оформить"),
    ("проверить", "проверка", "проверьте", "проверки"),
)
SPECIFIC_CONCEPT_WORDS = (
    ("усн", "упрощенка"),
    ("аусн", "автоусн"),
    ("самозанятость", "самозанятый", "нпд"),
    ("ozon", "озон"),
    ("wildberries", "вайлдберриз"),
    ("маркетплейс", "маркетплейсы", "селлер", "ozon", "озон", "wildberries", "вайлдберриз"),
)
PROFILE_STAGE_WORDS = {
    "готовится к запуску": ("запуск", "открытие", "начинающий"),
    "уже работает": ("работает", "действующий", "действующей", "действующая"),
    "развивается": ("развитие", "расширение", "рост", "масштабирование"),
}


def _stem(word: str) -> str:
    if len(word) <= 4 or not re.fullmatch(r"[а-я]+", word):
        return word
    stem = ENDING.sub("", word)
    return stem if len(stem) >= 3 else word


def _terms(text: str) -> frozenset[str]:
    words = WORD.findall(text.casefold().replace("ё", "е"))
    return frozenset(_stem(word) for word in words if word not in STOP_WORDS and len(word) >= 2)


CONCEPTS = tuple(frozenset(_stem(word) for word in group) for group in CONCEPT_WORDS)
SPECIFIC_CONCEPTS = tuple(frozenset(_stem(word) for word in group) for group in SPECIFIC_CONCEPT_WORDS)
MARKETPLACE_CONCEPT = SPECIFIC_CONCEPTS[-1]
TAX_TERMS = _terms("налог налоги налоговый налогообложение")
_AD_SUBJECT = r"(?:реклама|кампания|объявление|она|оно)"
_AD_OUTCOME_QUESTION = re.compile(
    rf"как (?:понять|узнать|проверить|оценить),?\s+(?:работает ли (?P<working>{_AD_SUBJECT})|"
    rf"(?:приводит|привела|приносит|принесла|да[её]т) ли (?P<outcome>{_AD_SUBJECT}) "
    r"(?:новых? )?(?:гостей|клиентов|покупателей|заказы|заявки|звонки|брони|визиты))", re.I
)
_AD_RESULT_QUESTION = re.compile(
    r"как (?:измерить|оценить|проверить|посчитать) (?:е[её]|его) (?:результат|эффективность)", re.I
)
_AD_RUNNING = re.compile(
    r"\b(?:реклама|кампания|объявление)\s+(?:уже\s+)?(?:ид[её]т|запущен[ао]?|работает)\b"
    r"|\bзапустил[аи]?\s+(?:рекламу|кампанию|объявление)\b", re.I
)
_AD_CHANNEL = re.compile(
    r"\b(?:реклам\w*|кампани\w*|объявлени\w*)\s+(?:на|в|через|для)\s+\w+", re.I
)
_AD_CONSTRAINT = re.compile(r"\bбез\s+(?:бюджет\w*|доступ\w*|статистик\w*|данных)\b", re.I)


def _focus_ad_question(query: str) -> str:
    """Resolve a generic measurement question without discarding named constraints."""
    clauses = re.split(r"(?<=[.!?])\s+|\n+", query.strip())
    question = clauses[-1].rstrip("?! .")
    outcome = _AD_OUTCOME_QUESTION.fullmatch(question)
    refers_to_ad = outcome and (outcome["working"] or outcome["outcome"]).casefold() not in ("она", "оно")
    if not refers_to_ad:
        if not (outcome or _AD_RESULT_QUESTION.fullmatch(question)):
            return query
        if len(clauses) < 2 or not _AD_RUNNING.search(clauses[-2]):
            return query
    if (any("?" in clause for clause in clauses[:-1]) or INITIALISM.search(query)
            or _AD_CHANNEL.search(query) or _AD_CONSTRAINT.search(query)
            or _terms(query) & (MARKETPLACE_CONCEPT | TAX_TERMS)):
        return query
    return "Как оценить эффективность рекламы?"


@dataclass(frozen=True)
class Evidence:
    title: str
    url: str
    passage: str
    heading: str = ""


@dataclass(frozen=True)
class Material:
    title: str
    url: str
    kind: str
    price_display: str
    description: str
    reason: str
    price: int = 0

    @property
    def is_paid(self) -> bool:
        return self.price > 0


@dataclass(frozen=True)
class SearchResult:
    evidence: tuple[Evidence, ...] = ()
    recommendations: tuple[Material, ...] = ()
    needs_clarification: bool = False


@dataclass(frozen=True)
class _Passage:
    heading: str
    text: str
    terms: frozenset[str]
    heading_terms: frozenset[str]


@dataclass(frozen=True)
class _Document:
    title: str
    url: str
    kind: str
    price: int
    description: str
    title_terms: frozenset[str]
    metadata_terms: frozenset[str]
    terms: frozenset[str]
    passages: tuple[_Passage, ...]


def _parse_document(value: dict, *, is_demo: bool) -> _Document:
    title, url, kind, price = (value[key] for key in ("title", "url", "kind", "price"))
    if kind not in {"article", "course"} or type(price) is not int or price < 0 or not title:
        raise ValueError("Некорректная карточка в каталоге.")
    if is_demo:
        if url:
            raise ValueError("У учебной карточки не должно быть внешней ссылки.")
    else:
        parsed = urlsplit(url)
        expected_prefix = "/articles/" if kind == "article" else "/courses/"
        if parsed.scheme != "https" or parsed.netloc != OFFICIAL_HOST or not parsed.path.startswith(expected_prefix):
            raise ValueError("Карточка должна вести на официальную страницу Альфа-Курса.")

    description = value.get("description", "")
    title_terms = _terms(title)
    metadata_terms = _terms(" ".join([title, description, *value.get("tags", [])]))
    # Full text is searchable only for public, free articles.
    passages = tuple(
        _Passage(p.get("heading", ""), p["text"], _terms(p["text"]), _terms(p.get("heading", "")))
        for p in value.get("passages", [])
    ) if kind == "article" and price == 0 else ()
    terms = metadata_terms.union(*(p.terms | p.heading_terms for p in passages))
    return _Document(title, url, kind, price, description, title_terms, metadata_terms, terms, passages)


class Catalog:
    def __init__(self, path: str | Path):
        path = Path(path)
        with path.open(encoding="utf-8") as file:
            first_line = file.readline()
            if not first_line:
                raise ValueError("Файл каталога пуст.")
            header = json.loads(first_line)
            if header.get("format") != 1 or header.get("mode") not in {"case", "demo"}:
                raise ValueError("Формат каталога не поддерживается. Пересоберите его через catalog_build.py.")
            self.is_demo = header["mode"] == "demo"
            self._documents = tuple(
                _parse_document(json.loads(line), is_demo=self.is_demo) for line in file if line.strip()
            )
        if not self._documents:
            raise ValueError("В каталоге отсутствуют материалы.")
        document_frequency = Counter(term for doc in self._documents for term in doc.terms)
        self._weights = {
            term: 1 + math.log((len(self._documents) + 1) / (count + 1))
            for term, count in document_frequency.items()
        }
        self.article_count = sum(bool(doc.passages) for doc in self._documents)
        self.material_count = len(self._documents)

    def _match(self, query: frozenset[str], terms: frozenset[str]) -> tuple[float, int]:
        exact = query & terms
        weight = sum(min(self._weights.get(term, 1.0), 4.0) for term in sorted(exact))
        concepts = sum(
            bool((query - exact) & group and not exact & group and terms & group)
            for group in CONCEPTS
        )
        return weight + concepts * 0.7, len(exact) + concepts

    def search(self, query: str, context: dict[str, str] | None = None) -> SearchResult:
        focused = _focus_ad_question(query)
        if focused != query:
            goal = (context or {}).get("goal", "")
            if isinstance(goal, str) and goal.startswith("число "):
                metric = goal.removeprefix("число ")
                result = self._search(f"эффективность рекламы {metric}", context)
                if result.evidence:
                    return result
            result = self._search(focused, context)
            if result.evidence:
                return result
        return self._search(query, context)

    def _search(self, query: str, context: dict[str, str] | None) -> SearchResult:
        query_terms = _terms(query)
        if not query_terms:
            return SearchResult(needs_clarification=True)
        context = context or {}
        sector_terms = _terms(context.get("sector", "")) if isinstance(context.get("sector"), str) else frozenset()
        context_terms = _terms(" ".join(
            value for value in context.values() if isinstance(value, str)
        )) - query_terms
        stage_terms = _terms(" ".join(PROFILE_STAGE_WORDS.get(context.get("stage", ""), ())))
        relevant_concepts = tuple(group for group in SPECIFIC_CONCEPTS if query_terms & group)
        uncommon_initialisms = {
            term for term in _terms(" ".join(INITIALISM.findall(query)))
            if self._weights.get(term, 7) > 5.7
        }
        is_marketplace_tax_query = (
            bool(query_terms & MARKETPLACE_CONCEPT)
            and bool(query_terms & (SPECIFIC_CONCEPTS[0] | TAX_TERMS))
        )
        evidence = []
        cards = []
        min_matches = min(len(query_terms), max(2, math.ceil(len(query_terms) * 0.5)))

        for doc in self._documents:
            title_score, title_count = self._match(query_terms, doc.title_terms)
            metadata_score, metadata_count = self._match(query_terms, doc.metadata_terms)
            context_score, _ = self._match(context_terms, doc.metadata_terms)
            context_bonus = min(context_score, 9.0) * 0.6
            best_passage = None
            if uncommon_initialisms - doc.terms:
                continue
            for passage in doc.passages:
                body_score, body_count = self._match(query_terms, passage.terms)
                heading_score, _ = self._match(query_terms, passage.heading_terms)
                combined_terms = passage.terms | passage.heading_terms | doc.title_terms
                if uncommon_initialisms - combined_terms:
                    continue
                if any(not group & combined_terms for group in relevant_concepts):
                    continue
                if is_marketplace_tax_query and not TAX_TERMS & (passage.heading_terms | doc.title_terms):
                    continue
                _, combined_count = self._match(query_terms, combined_terms)
                exact_count = len(query_terms & combined_terms)
                if not body_count or combined_count < min_matches or exact_count < min_matches - 1:
                    continue
                score = (
                    body_score * 2.0 + heading_score + title_score * 1.8 + metadata_score * 0.35
                    + combined_count * 1.15 + context_bonus
                )
                if stage_terms & doc.metadata_terms and title_count >= 2:
                    score += 4.0
                if best_passage is None or score > best_passage[0]:
                    best_passage = (score, passage)
            if best_passage:
                score, passage = best_passage
                evidence.append((score, Evidence(doc.title, doc.url, passage.text, passage.heading)))
            if not best_passage and (
                uncommon_initialisms - doc.metadata_terms
                or any(not group & doc.metadata_terms for group in relevant_concepts)
            ):
                continue
            if is_marketplace_tax_query and not TAX_TERMS & doc.metadata_terms:
                continue
            if doc.price and (title_count < 2 or len(query_terms & doc.metadata_terms) < 2):
                continue
            if metadata_count >= min_matches or best_passage:
                score = metadata_score + title_score * 2.2 + context_bonus
                if stage_terms & doc.metadata_terms and title_count >= 2:
                    score += 4.0
                if best_passage:
                    score += best_passage[0] * 0.4
                if score:
                    cards.append((score, doc))

        evidence.sort(key=lambda item: (-item[0], item[1].title))
        cards.sort(key=lambda item: (-item[0], item[1].title))
        best_evidence = tuple(item[1] for item in evidence[:2])
        selected = [
            next((item[1] for item in cards if item[1].kind == "article"), None),
            next((item[1] for item in cards if item[1].kind == "course" and not item[1].price), None),
            next((item[1] for item in cards if item[1].kind == "course" and item[1].price), None),
        ]
        recommendations = tuple(
            Material(
                doc.title,
                doc.url,
                doc.kind,
                f"{doc.price:,} ₽".replace(",", " ") if doc.price else "Бесплатно",
                doc.description,
                "Подходит к текущему этапу бизнеса." if doc.metadata_terms & stage_terms
                else "Есть материал для вашей сферы." if doc.metadata_terms & sector_terms
                else "Подходит к теме запроса.",
                doc.price,
            )
            for doc in selected if doc is not None
        )
        # A one-word subject can start a consultation, but the goal needs clarification.
        return SearchResult(best_evidence, recommendations, needs_clarification=len(query_terms) == 1)
