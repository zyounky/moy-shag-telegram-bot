"""Offline release evaluation with synthetic conversations and optional local case retrieval."""

import argparse
from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
import re
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assistant_bot.advice import catalog_context, infer_profile
from assistant_bot.bot import Assistant
from assistant_bot.knowledge import Catalog, DEMO_CATALOG, Evidence, Material, SearchResult
from assistant_bot.llm import ResponseGenerator
from assistant_bot.query_language import russian_search_query
from assistant_bot.reminders import ReminderStore
from assistant_bot.state import ConversationStore


EVALUATION = ROOT / "evaluation"
AD_QUERY = "Как оценить эффективность рекламы кофейни?"
MARKETPLACE_QUERY = "Как сверить продажи с отчётом маркетплейса Ozon?"
SOURCE = Evidence(
    "Учебная статья о продажах на площадке",
    "https://example.org/learn/marketplace",
    "Сравните продажи, возвраты и перечисления за один и тот же период.",
)
CARDS = (
    Material("Учебная бесплатная памятка", "https://example.org/learn/checklist", "document",
             "Бесплатно", "Пример карточки памятки.", "Для сверки."),
    Material("Учебный платный курс", "https://example.org/learn/course", "course",
             "1 000 ₽", "Пример платной карточки.", "Для продолжения.", 1000, "paid"),
)


def require(condition, description):
    if not condition:
        raise AssertionError(description)


def load_synthetic(directory):
    path = directory / "synthetic.jsonl"
    examples = (EVALUATION / "synthetic_catalog.jsonl").read_text(encoding="utf-8").splitlines()
    base = DEMO_CATALOG.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(base + examples[1:]) + "\n", encoding="utf-8")
    return Catalog(path)


class Outbox:
    def __init__(self):
        self.messages = []

    def send(self, chat, text, rows=None):
        self.messages.append((chat, text, rows or []))

    def buttons(self):
        return [button for row in self.messages[-1][2] for button in row]


class Conversation:
    def __init__(self, database, catalog, generator=None):
        self.store = ConversationStore(database)
        self.reminders = ReminderStore(database)
        self.outbox = Outbox()
        self.now = datetime(2026, 11, 15, 12, tzinfo=UTC)
        self.bot = Assistant(self.outbox, catalog, self.store, self.reminders,
                             generator=generator, now=lambda: self.now)

    def message(self, text, *, first=False):
        self.bot.message(1, text, require_language=first)

    def click(self, callback):
        require(any(button.get("callback_data") == callback for button in self.outbox.buttons()),
                "Ожидаемая кнопка не показана")
        self.bot.callback(1, callback)

    def state(self):
        return self.store.get(1)


class OfflineTransport:
    def __init__(self):
        self.attempts = 0

    def open(self, *args, **kwargs):
        self.attempts += 1
        raise OSError("Synthetic API outage")


def unavailable_generator(budget=2):
    generator = ResponseGenerator("xai", "synthetic-offline-token", "synthetic-test-model",
                                  max_requests=budget, stream=False)
    generator.opener = OfflineTransport()
    return generator


def fixture_catalog(evidence=(SOURCE,), search_queries=None):
    def search(question, context):
        if search_queries is not None:
            search_queries.append(question)
        return SearchResult(evidence, CARDS)
    return SimpleNamespace(is_demo=True, search=search)


def check_profile(dialog, case):
    dialog.message("/start")
    dialog.click("lang:ru")
    dialog.click("profile:show")
    dialog.click(f"stage:{case['stage']}")
    dialog.message(AD_QUERY)
    state = dialog.state()
    require(state.profile.get("stage") == case["stage"], "Стадия не сохранена")
    require(state.profile.get("sector") == "Кофейня", "Сфера не извлечена из запроса")
    require(case["action"] in state.proposal.get("title", "").casefold(),
            "Следующий шаг не соответствует профилю")


def check_context(dialog, case, search_queries):
    dialog.message(AD_QUERY)
    require(dialog.state().pending.get("kind") == "clarify", "Нет ожидаемого уточнения")
    if case["flow"] == "new_topic":
        replacement = "Как нанять первого сотрудника?"
        dialog.message(replacement)
        require(search_queries[-1] == replacement, "Новый вопрос унаследовал старый контекст")
        require(dialog.state().topic == replacement, "Тема не обновилась")
    elif case["flow"] == "cancel":
        dialog.message("/cancel")
        require(not dialog.state().pending, "Уточнение осталось после отмены")
        require(len(search_queries) == 1, "Отмена запустила поиск")
    else:
        dialog.message("ВКонтакте")
        dialog.message("Цель — заказы")
        require(all(term in search_queries[-1] for term in (AD_QUERY, "ВКонтакте", "заказы")),
                "Контекст потерян после двух уточнений")


def check_obstacle(dialog):
    dialog.message(AD_QUERY)
    proposal = dialog.state().proposal["id"]
    dialog.click(f"action:take:{proposal}")
    dialog.click(f"action:blocked:{proposal}")
    dialog.message("У меня нет данных по кампании.")
    state = dialog.state()
    require(state.action["id"] == proposal and state.action["status"] == "blocked",
            "Препятствие затёрло активную задачу")
    require("доступный показатель" in state.proposal.get("title", ""),
            "Нет выполнимого действия при отсутствии данных")


def check_first_reminder(dialog):
    dialog.message("Remind me to send the report", first=True)
    dialog.click("lang:en")
    pending = dialog.state().pending
    require(pending.get("kind") == "reminder" and pending.get("title") == "send the report",
            "Первый запрос потерян при выборе языка")
    require(any(button.get("callback_data") == "rem:type:business" for button in dialog.outbox.buttons()),
            "Не открылась настройка напоминания")


def check_reminder(dialog):
    dialog.message("/remind")
    dialog.click("rem:type:business")
    dialog.message("Отправить отчёт")
    dialog.click("rem:in:1")
    dialog.click("rem:freq:once")
    dialog.click("rem:confirm")
    reminder, = dialog.reminders.list_active(1)
    require(reminder.frequency == "once", "Разовое напоминание не сохранено")
    dialog.click("reminders")
    dialog.click(f"manage:edit:{reminder.id}")
    dialog.click(f"manage:set:{reminder.id}:weekly")
    dialog.now += timedelta(minutes=1)
    dialog.bot.deliver_reminders()
    require("Отправить отчёт" in dialog.outbox.messages[-1][1], "Не доставлено напоминание")
    require(dialog.reminders.get(1, reminder.id).next_run == dialog.now + timedelta(weeks=1),
            "Повтор не запланирован")
    dialog.click("reminders")
    dialog.click(f"manage:off:{reminder.id}")
    require(not dialog.reminders.list_active(1), "Напоминание не отключилось")


def check_material_fallback(dialog):
    links = [button["url"] for button in dialog.outbox.buttons() if "url" in button]
    require(all(card.url in links for card in CARDS), "Бесплатная или платная карточка пропала")
    require(len(links) == len(set(links)), "Ссылки повторяются")
    require(not dialog.state().proposal, "Технический отказ создал консультацию или задачу")


def check_api(dialog, flow, generator):
    dialog.message(MARKETPLACE_QUERY)
    if flow == "metadata":
        require(generator.opener.attempts == 0, "Карточка без текста вызвала API")
    else:
        require(generator.last_status == "unavailable", "Ошибка транспорта не распознана")
        require(generator.opener.attempts == 1, "Неверное число попыток транспорта")
        if flow == "budget":
            dialog.message("Как различать продажи и выплаты Ozon?")
            require(generator.last_status == "budget", "Бюджет не остановил второй запрос")
            require(generator.opener.attempts == 1, "Бюджет превысил одну попытку")
    check_material_fallback(dialog)


def run_flow(case, directory, catalog):
    flow = case["flow"]
    database = directory / f"{case['id']}.db"
    if flow in {"clarifications", "new_topic", "cancel"}:
        queries = []
        generator = SimpleNamespace(last_status="insufficient", last_metrics={},
                                    last_clarification="В каком канале размещается реклама?",
                                    generate=lambda *args, **kwargs: None)
        check_context(Conversation(database, fixture_catalog(search_queries=queries), generator), case, queries)
    elif flow in {"api_failure", "budget", "metadata"}:
        generator = unavailable_generator(1 if flow == "budget" else 2)
        sources = () if flow == "metadata" else (SOURCE,)
        check_api(Conversation(database, fixture_catalog(sources), generator), flow, generator)
    else:
        dialog = Conversation(database, catalog)
        if flow == "profile":
            check_profile(dialog, case)
        else:
            {"obstacle": check_obstacle, "first_reminder": check_first_reminder,
             "reminder": check_reminder}[flow](dialog)


def run_retrieval(case, catalog, mode):
    query = russian_search_query(case["query"])
    profile = dict(case.get("profile", {}))
    infer_profile(query, profile)
    context = catalog_context(profile)
    started = perf_counter()
    result = catalog.search(query, context)
    elapsed_ms = round((perf_counter() - started) * 1000, 3)
    expect = case["expect"]
    if isinstance(expect, dict):
        expect = expect[mode]
    pattern = case.get(mode)

    def matches(item):
        return bool(pattern and re.search(pattern, item.title.replace("\xa0", " "), re.I))

    titles_match = [matches(item) for item in result.evidence[:3]]
    scope = [item.content_scope for item in result.evidence if matches(item)]
    cards_match = any(matches(item) for item in result.recommendations)
    checks = {
        "evidence": any(titles_match) and not result.needs_clarification,
        "clarification": result.needs_clarification,
        "missing": not result.evidence and not result.needs_clarification,
        # Synthetic card has no searchable text. The official record may have an
        # excerpt and can still be inspected for whether the attached file is absent.
        "metadata": cards_match and not result.evidence,
        "bounded": (bool(scope or cards_match)
                    and all(value == "document_description" for value in scope)
                    and not result.needs_clarification),
    }
    return {
        "expect": expect,
        "passed": checks[expect],
        "top1_title_match": bool(titles_match and titles_match[0]),
        "top3_title_match": any(titles_match),
        "recommended_title_match": cards_match,
        "needs_clarification": result.needs_clarification,
        "evidence_count": len(result.evidence),
        "matching_source_scopes": scope,
        "search_ms": elapsed_ms,
    }


def evaluate(cases, catalogue_path):
    with TemporaryDirectory(prefix="moy-shag-evaluation-") as work:
        directory = Path(work)
        synthetic = load_synthetic(directory)
        catalogue = Catalog(catalogue_path) if catalogue_path else synthetic
        if catalogue_path and catalogue.is_demo:
            raise ValueError("--catalog expects a local catalogue in case mode.")
        mode = "official" if catalogue_path else "synthetic"
        retrievals = []
        checks = []
        for case in cases:
            record = {key: case[key] for key in ("id", "area", "review")}
            if "flow" in case:
                try:
                    run_flow(case, directory, synthetic)
                except (AssertionError, KeyError, ValueError) as error:
                    record.update(passed=False, issue=str(error))
                else:
                    record["passed"] = True
                checks.append(record)
            else:
                retrievals.append({**record, **run_retrieval(case, catalogue, mode)})
        timings = sorted(row["search_ms"] for row in retrievals)
        return {
            "mode": mode,
            "catalogue": {"materials": catalogue.material_count,
                          "with_text": catalogue.text_material_count},
            "logic": {"passed": sum(row["passed"] for row in checks),
                      "total": len(checks), "cases": checks},
            "retrieval": {"passed": sum(row["passed"] for row in retrievals),
                          "total": len(retrievals), "cases": retrievals},
            "latency": {"measure": "local search only, with index built before timing",
                        "samples": len(timings),
                        "p50_ms": round(statistics.median(timings), 3),
                        "p90_ms": timings[math.ceil(len(timings) * .9) - 1]},
            "generation_quality": {"status": "not_run", "api_requests": 0,
                                   "transport": "offline for all logic scenarios"},
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, help="prepared local official JSONL (default: synthetic fixtures)")
    parser.add_argument("--report", type=Path, help="optional JSON output path; no catalogue text is written")
    args = parser.parse_args()
    cases = json.loads((EVALUATION / "scenarios.json").read_text(encoding="utf-8"))
    report = evaluate(cases, args.catalog)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for section in ("logic", "retrieval"):
        summary = report[section]
        print(f"{section}: {summary['passed']}/{summary['total']}")
        failures = ", ".join(row["id"] for row in summary["cases"] if not row["passed"])
        if failures:
            print(f"  failed: {failures}")
    print(json.dumps({key: report[key] for key in ("mode", "catalogue", "latency", "generation_quality")},
                     ensure_ascii=False))
    return int(any(report[key]["passed"] != report[key]["total"] for key in ("logic", "retrieval")))


if __name__ == "__main__":
    raise SystemExit(main())
