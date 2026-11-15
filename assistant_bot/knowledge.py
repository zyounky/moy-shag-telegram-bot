"""Local Russian morphology, BM25 retrieval and bounded source context."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import pymorphy3


DEMO_CATALOG = Path(__file__).with_name("demo_catalog.jsonl")
OFFICIAL_HOST = "kurs.alfabank.ru"
PREFIXES = {"article": "/articles/", "course": "/courses/", "lesson": "/courses/",
            "document": "/documents/", "glossary": "/glossary/", "webinar": "/webinars/",
            "podcast": "/podcasts/", "calculator": "/calculators/"}
MORPH = pymorphy3.MorphAnalyzer()
WORDS = re.compile(r"[а-яёa-z0-9]+", re.I)
STOP = set("а без бы быть в вам ваш вас во вот все вы где да для до его ее если есть же за и из или им их к как ко когда кто куда ли мне мой мы на над надо нас наш не него нее нет ни но о об он она они от по под пока после при про с свое себя со так такое там то того тоже тут ты у уже чем что чтобы эта эти это этом этот я можно нужно подскажи расскажи объясни помочь помоги пожалуйста сделать делать посоветуйте начать узнать хочу планирую собираюсь самый свой который какой такой первый один мочь хотеть вопрос понять узнать составить рассчитать провести работать оценить проверить открыть выбрать нужный бизнес дело".split())
ALIASES = {
    "озон": "ozon", "вайлдберриз": "wildberries", "wb": "wildberries",
    "упрощенка": "усн", "автоусн": "аусн", "нпд": "самозанятость",
    "самозанятый": "самозанятость", "налогообложение": "налог", "налоговый": "налог",
    "рекламный": "реклама", "продвижение": "реклама", "маркетинг": "реклама",
    "эффективность": "результат",
    "покупатель": "клиент", "персонал": "сотрудник", "работник": "сотрудник",
    "нанять": "найм", "нанимать": "найм", "рекрутинг": "найм",
    "зарегистрироваться": "регистрация", "зарегистрировать": "регистрация",
    "конкурентный": "конкурент", "денежный": "деньги", "средство": "деньги",
    "директ": "direct",
}
SPECIFIC = {"ozon", "wildberries", "усн", "аусн", "ндс", "ндфл", "самозанятость", "яндекс", "direct"}


@lru_cache(maxsize=100000)
def _lemma(word):
    word = word.casefold().replace("ё", "е")
    normal = MORPH.parse(word)[0].normal_form if re.fullmatch(r"[а-я]+", word) else word
    return ALIASES.get(normal, ALIASES.get(word, normal))


def _tokens(text):
    text = re.sub(r"бизнес[\s-]+план\w*", "бизнесплан", text, flags=re.I)
    text = re.sub(r"денежн\w*\s+поток\w*|cash\s*flow", "cashflow", text, flags=re.I)
    return [_lemma(word) for word in WORDS.findall(text)
            if len(word) >= 2 and word.casefold().replace("ё", "е") not in STOP
            and _lemma(word) not in STOP]


def _query_tokens(text):
    tokens = set(_tokens(text))
    if "сотрудник" in tokens and ("удержать" in tokens or "текучесть" in tokens):
        tokens.update(("увольняться", "мотивация", "текучесть"))
    if "сотрудник" in tokens and "найм" in tokens:
        tokens.update(("привлечь", "оформить"))
    if "окупаться" in tokens:
        tokens.update(("окупаемость", "эффективность"))
    return tokens


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
_GLOSSARY_ACRONYM = re.compile(r"^([А-ЯЁA-Z]{2,8})(?:\s*[(:—–-]|$)|\(([А-ЯЁA-Z]{2,8})\)\s*$")
_BROAD_TOPICS = {"маркетинг", "реклама", "продажи", "финансы", "сотрудники", "учет"}


def _literal_term(text):
    return " ".join(text.strip().strip("?! .").casefold().replace("ё", "е").split())


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
    if (any("?" in clause for clause in clauses[:-1]) or re.search(r"\b[А-ЯЁA-Z]{2,5}\b", query)
            or _AD_CHANNEL.search(query) or _AD_CONSTRAINT.search(query)
            or set(_tokens(query)) & (SPECIFIC | {"маркетплейс", "налог"})):
        return query
    return "Как оценить эффективность рекламы?"


@dataclass(frozen=True)
class Evidence:
    title: str
    url: str
    passage: str
    heading: str = ""
    kind: str = "article"
    content_scope: str = "public_text"
    material_id: str = ""


@dataclass(frozen=True)
class Material:
    title: str
    url: str
    kind: str
    price_display: str
    description: str
    reason: str
    price: int = 0
    access_type: str = "free"

    @property
    def is_paid(self):
        return self.price > 0 or self.access_type != "free"


@dataclass(frozen=True)
class SearchResult:
    evidence: tuple[Evidence, ...] = ()
    recommendations: tuple[Material, ...] = ()
    needs_clarification: bool = False
    exact_definition: bool = False


class Catalog:
    def __init__(self, path):
        with Path(path).open(encoding="utf-8") as source:
            header = json.loads(source.readline())
            if header.get("format") != 1 or header.get("mode") not in {"demo", "case"}:
                raise ValueError("Формат каталога не поддерживается.")
            self.is_demo = header["mode"] == "demo"
            rows = [json.loads(line) for line in source if line.strip()]
        if not rows:
            raise ValueError("В каталоге отсутствуют материалы.")
        self._docs = []
        self._chunks = []
        self._postings = defaultdict(set)
        self._df = Counter()
        self._glossary_terms = defaultdict(set)
        for row in rows:
            kind, url, price = row["kind"], row["url"], row["price"]
            if kind not in PREFIXES or type(price) is not int or price < 0 or not row.get("title"):
                raise ValueError("Некорректная карточка в каталоге.")
            parsed = urlsplit(url)
            if (self.is_demo and url) or (not self.is_demo and (
                    parsed.scheme != "https" or parsed.netloc != OFFICIAL_HOST
                    or not parsed.path.startswith(PREFIXES[kind]))):
                raise ValueError("Карточка должна вести на официальную страницу Альфа-Курса.")
            access = row.get("access_type", "paid" if price else "free")
            # A zero price is not enough: subscription text is excluded too.
            allowed = kind != "course" and access == "free" and price == 0
            passages = row.get("passages", []) if allowed else []
            doc_id = len(self._docs)
            title = Counter(_tokens(row["title"]))
            meta = Counter(_tokens(" ".join([row["title"], row.get("description", ""), *row.get("tags", [])])))
            doc = {"row": row, "title": title, "meta": meta, "terms": set(meta), "chunks": []}
            for passage in passages:
                text = passage.get("text", "").strip()
                if not text:
                    continue
                terms = Counter(_tokens(text))
                heading = Counter(_tokens(passage.get("heading", "")))
                chunk_id = len(self._chunks)
                doc["chunks"].append(chunk_id)
                doc["terms"].update(terms)
                doc["terms"].update(heading)
                self._chunks.append({"doc": doc_id, "text": text, "heading": passage.get("heading", ""),
                                     "terms": terms, "head_terms": heading, "length": sum(terms.values())})
                for term in terms.keys() | heading.keys() | title.keys():
                    self._postings[term].add(chunk_id)
            self._df.update(doc["terms"])
            self._docs.append(doc)
            if kind == "glossary" and doc["chunks"]:
                title_term = _literal_term(row["title"])
                self._glossary_terms[title_term].add(doc_id)
                acronym = _GLOSSARY_ACRONYM.search(row["title"].strip())
                if acronym:
                    self._glossary_terms[_literal_term(acronym[1] or acronym[2])].add(doc_id)
        self._avg_length = sum(c["length"] for c in self._chunks) / max(1, len(self._chunks))
        self.article_count = sum(d["row"]["kind"] == "article" and bool(d["chunks"]) for d in self._docs)
        self.text_material_count = sum(bool(d["chunks"]) for d in self._docs)
        self.material_count = len(self._docs)
        self.kind_counts = dict(Counter(d["row"]["kind"] for d in self._docs))

    def _idf(self, term):
        count = self._df.get(term, 0)
        return math.log(1 + (len(self._docs) - count + .5) / (count + .5))

    def _score(self, query, counts, length=None):
        length = sum(counts.values()) if length is None else length
        norm = 1.2 * (.25 + .75 * length / max(1, self._avg_length))
        return sum(self._idf(t) * counts[t] * 2.2 / (counts[t] + norm)
                   for t in query if counts.get(t))

    def _packet(self, doc, ranked_chunks, broad, query=(), budget=3600, procedure=False, define=False):
        best = ranked_chunks[0][1]
        ids = doc["chunks"]
        pos = ids.index(best)
        # Prefer other relevant sections over arbitrary neighbouring paragraphs.
        # An adjacent block is still useful for definitions, conditions and lists.
        steps = [i for i in ids if re.search(r"^шаг\s*\d|пошагов\w*\s+инструкц", self._chunks[i]["heading"], re.I)] if broad and procedure else []
        formulae = [i for i in ids if re.search(r"формул|методик\w*\s+расч", self._chunks[i]["heading"], re.I)] if broad and define else []
        formula_context = [j for i in formulae for j in ids[max(0, ids.index(i) - 1):ids.index(i) + 3]]
        selected = list(dict.fromkeys(steps + ([ids[0]] + formulae + formula_context if formulae else []) + [best] +
                                     ([ids[0]] if broad else []) +
                                     [i for score, i in ranked_chunks[1:8]
                                      if score >= ranked_chunks[0][0] * .55] +
                                     ids[max(0, pos - 1):pos + 3]))
        parts = {}
        size = 0
        for chunk_id in selected:
            text = self._chunks[chunk_id]["text"]
            heading = self._chunks[chunk_id]["heading"].strip()
            if chunk_id in steps and heading and not text.startswith(heading):
                text = heading + "\n" + text
            if text in parts.values():
                continue
            remaining = budget - size - (2 if parts else 0)
            if len(text) > remaining:
                # Never silently drop an oversized best chunk. Extract complete
                # paragraphs/sentences in original order; no generated summary.
                if chunk_id != best or remaining < 150:
                    continue
                text = self._excerpt(text, query, remaining)
            if not text:
                continue
            parts[chunk_id] = text
            size += len(text) + 2
        row = doc["row"]
        return Evidence(row["title"], row["url"], "\n\n".join(parts[i] for i in sorted(parts)),
                        self._chunks[best]["heading"], row["kind"],
                        row.get("content_scope", "public_text"), row.get("id", ""))

    def _excerpt(self, text, query, budget):
        units = [s.strip() for s in re.split(r"\n+|(?<=[.!?])\s+", text) if s.strip()]
        # Keep the adjacent sentence too: it can contain a qualification, a
        # formula or an example that has no overlapping query words.
        ranked = sorted(range(len(units)),
                        key=lambda i: (-self._score(query, Counter(_tokens(units[i]))), i))
        selected = set()
        size = 0
        for anchor in ranked:
            for i in (anchor, anchor + 1, anchor - 1):
                if i < 0 or i >= len(units) or i in selected:
                    continue
                if size + len(units[i]) + 1 <= budget:
                    selected.add(i)
                    size += len(units[i]) + 1
        return "\n".join(units[i] for i in sorted(selected))

    def search(self, query: str, context: dict[str, str] | None = None) -> SearchResult:
        term = _literal_term(query)
        exact_definitions = self._glossary_terms.get(term, ())
        if len(exact_definitions) == 1 and term not in _BROAD_TOPICS:
            return self._search(query, context, exact_definition=next(iter(exact_definitions)))
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

    def _search(self, query, context=None, *, exact_definition=None):
        query = re.sub(r"простыми словами|простым языком|понятным языком", "", query, flags=re.I)
        original = set(_tokens(query))
        if "сотрудник" in original and "удержать" in original:
            original.remove("удержать")
            original.add("увольняться")
        query_sector = original & {"кофейня", "кафе", "магазин", "салон", "студия"}
        if len(original - query_sector) >= 2:
            original -= query_sector
        terms = _query_tokens(query)
        terms.update(original)
        if not terms:
            return SearchResult(needs_clarification=True)
        context = context or {}
        specific = original & SPECIFIC
        define = exact_definition is not None or bool(re.search(r"что такое|что значит|объясни|определение", query, re.I))
        procedure = not define and bool(re.search(r"\bкак\b|с чего|порядок|инструкц|этап|раздел|что делать", query, re.I))
        candidates = set().union(*(self._postings.get(t, set()) for t in terms))
        chunk_scores = defaultdict(list)
        for chunk_id in candidates:
            chunk = self._chunks[chunk_id]
            doc = self._docs[chunk["doc"]]
            if specific - doc["terms"]:
                continue
            score = self._score(terms, chunk["terms"], chunk["length"])
            score += 1.5 * self._score(terms, chunk["head_terms"])
            chunk_scores[chunk["doc"]].append((score, chunk_id))
        ranked = []
        cards = []
        original_weight = sum(self._idf(t) for t in original) or 1
        sector = set(_tokens(context.get("sector", ""))) | query_sector
        stage = context.get("stage", "")
        stage_terms = set(_tokens({"готовится к запуску": "запуск открытие начинающий",
                                  "уже работает": "действующий работает", "развивается": "развитие масштабирование"}.get(stage, "")))
        for doc_id, doc in enumerate(self._docs):
            if exact_definition is not None and doc_id != exact_definition:
                continue
            if specific - doc["terms"]:
                continue
            coverage = sum(self._idf(t) for t in original & doc["terms"]) / original_weight
            if coverage < .55:
                continue
            title_coverage = sum(self._idf(t) for t in original & doc["title"].keys()) / original_weight
            chunks = sorted(chunk_scores.get(doc_id, []), reverse=True)
            best = chunks[0][0] if chunks else 0
            score = 3.0 * self._score(terms, doc["title"]) + .5 * self._score(terms, doc["meta"]) + best
            score *= .5 + coverage
            score += 5 * title_coverage
            score += min(2, len(sector & doc["meta"].keys()))
            score += 4 * len(specific & doc["title"].keys())
            score += 4 if stage_terms & doc["meta"].keys() else 0
            if define and doc["row"]["kind"] == "glossary":
                score += 8 * title_coverage
            if "найм" in terms and "сотрудник" in terms and "найм" in doc["title"]:
                score += 5
            if "удержать" in original and "сотрудник" in original and "увольняться" in doc["title"]:
                score += 10
            if chunks:
                ranked.append((score, doc_id, chunks, title_coverage >= .8))
            if score > 0:
                cards.append((score, doc_id))
        ranked.sort(key=lambda item: (-item[0], self._docs[item[1]]["row"]["title"]))
        evidence = []
        seen = set()
        for score, doc_id, chunks, broad in ranked:
            doc = self._docs[doc_id]
            if evidence and score < ranked[0][0] * .60:
                continue
            key = doc["row"]["url"] or doc["row"]["title"]
            if key not in seen:
                budget = 3600 if not evidence else 1600
                packet = self._packet(doc, chunks, broad, terms, budget, procedure, define)
                if not packet.passage:
                    continue
                evidence.append(packet)
                seen.add(key)
                # A direct glossary definition is sufficient on its own. Do not
                # pull loosely related how-to articles into a simple definition.
                if define and len(evidence) == 1 and broad and packet.kind == "glossary":
                    break
            if len(evidence) == 3:
                break
        cards.sort(reverse=True)
        selected = []
        selected_kinds = set()
        for _, doc_id in cards:
            row = self._docs[doc_id]["row"]
            access = row.get("access_type", "paid" if row["price"] else "free")
            group = "paid" if access != "free" else "course" if row["kind"] == "course" else "text"
            if group in selected_kinds:
                continue
            selected_kinds.add(group)
            price = row["price"]
            price_display = f"{price:,} ₽".replace(",", " ") if price else "По подписке" if access == "subscription" else "Бесплатно"
            reason = "Подходит к текущему этапу бизнеса." if stage_terms & self._docs[doc_id]["meta"].keys() else "Подходит к теме запроса."
            selected.append(Material(row["title"], row["url"], row["kind"], price_display,
                                     row.get("description", ""), reason, price, access))
        vague = (len(original) == 1 and not define and len(WORDS.findall(query)) <= 2
                 and (bool(cards) or _literal_term(query) in _BROAD_TOPICS))
        return SearchResult(tuple(evidence), tuple(selected), vague, exact_definition is not None)
