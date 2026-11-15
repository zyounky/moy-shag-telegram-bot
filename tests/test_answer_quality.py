import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from assistant_bot.advice import compose_advice, infer_profile
from assistant_bot.answer_policy import (budget_state, tax_regime_needed, unsupported_claim,
                                         unsupported_scope_claim)
from assistant_bot.bot import Assistant
from assistant_bot.knowledge import Catalog, Evidence, SearchResult
from assistant_bot.llm import COMPACT_PROMPT, SYSTEM_PROMPT, ResponseGenerator
from assistant_bot.reminders import ReminderStore
from assistant_bot.state import ConversationStore


BREAK_EVEN = Evidence(
    "Как рассчитать точку безубыточности",
    "",
    "Точка безубыточности = постоянные затраты / маржинальность. "
    "Маржинальность = (выручка − переменные затраты) / выручка × 100%.",
)
ROMI = Evidence(
    "Окупаемость рекламы",
    "",
    "ROMI оценивает окупаемость маркетинговых затрат. Запишите расходы на кампанию "
    "и финансовый результат, полученный от этой кампании за тот же период.",
)
KPI = Evidence(
    "Показатели эффективности менеджеров по продажам",
    "",
    "Для менеджеров по продажам в примере порог премии начинается с 80–85%.",
)


class AnswerQualityTests(unittest.TestCase):
    def make_catalog(self, *sources):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "catalog.jsonl"
        rows = [{"format": 1, "mode": "demo"}]
        rows.extend({"title": source.title, "url": "", "kind": source.kind, "price": 0,
                     "content_scope": source.content_scope, "passages": [{"text": source.passage}]}
                    for source in sources)
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
        return Catalog(path)

    def test_break_even_fraction_and_percent_give_same_ruble_result(self):
        for input_margin in ("доля маржи 0,4, то есть 40%", "маржинальность 40%",
                             "доля маржи — 0,4 (40%)"):
            with self.subTest(input_margin=input_margin):
                query = f"Какая выручка безубыточности? Постоянные затраты — 100 000 рублей, {input_margin}."
                advice = compose_advice(query, BREAK_EVEN, {})
                self.assertTrue(advice.local_complete)
                self.assertIn("250 000 ₽", advice.answer)
                self.assertIn("0,4", advice.answer)
                self.assertIn("40%", advice.answer)
                self.assertNotIn("2 500 ₽", advice.answer)
                self.assertIn("отклонение", advice.completion)

    def test_break_even_does_not_reinterpret_negative_or_ambiguous_inputs(self):
        for costs, margin, explanation in [
            ("-100 000 ₽", "маржинальность 40%", "отрицательными"),
            ("100 000 ₽", "доля маржи -0,4", "положительная доля"),
            ("100 000 ₽", "маржинальность 40", "Уточните"),
            ("100 000 ₽", "доля маржи 0,4, то есть 30%", "согласованную долю"),
            ("100 000 ₽", "маржинальность 0%", "положительная доля"),
        ]:
            with self.subTest(costs=costs, margin=margin):
                advice = compose_advice(f"Точка безубыточности: постоянные затраты {costs}, {margin}.", BREAK_EVEN, {})
                self.assertTrue(advice.local_complete)
                self.assertIn(explanation, advice.answer)
                self.assertNotIn("250 000 ₽", advice.answer)

    def test_break_even_direct_path_does_not_spend_model_attempt(self):
        catalog = self.make_catalog(BREAK_EVEN)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "state.db"
        outbox = Mock()
        generator = Mock()
        bot = Assistant(outbox, catalog, ConversationStore(path), ReminderStore(path), generator=generator)
        bot.message(1, "Какая точка безубыточности? Постоянные затраты 100 000 ₽, маржинальность 40%.")
        generator.generate.assert_not_called()
        self.assertIn("250 000 ₽", outbox.send.call_args.args[1])

    def test_sales_manager_bonus_threshold_is_not_a_storewide_rule(self):
        neutral = Evidence("KPI для сотрудников магазина", "", "Выберите измеримые показатели работы сотрудников.")
        catalog = self.make_catalog(KPI, neutral)
        query = "Как выставить KPI и премии для всех сотрудников магазина?"
        found = catalog.search(query).evidence
        self.assertTrue(found)
        self.assertNotIn(KPI.title, {source.title for source in found})
        self.assertTrue(unsupported_claim(query, "", "Установите порог 80–85% для всей команды."))
        self.assertFalse(unsupported_claim("Как премировать менеджеров по продажам?", "",
                                           "В примере есть порог 80–85% для этой роли."))
        self.assertNotIn("80–85%", compose_advice(query, neutral, {}).answer)

    def test_missing_leads_keeps_financial_question_and_other_data_open(self):
        cost_per_lead = Evidence("Как оценить заявки из рекламы", "", "Сравните количество заявок. Цена заявки — расходы на одну заявку.")
        catalog = self.make_catalog(cost_per_lead, ROMI)
        query = "Как оценить окупаемость рекламы, если заявки не записывали?"
        found = catalog.search(query).evidence
        self.assertEqual([source.title for source in found], [ROMI.title])
        self.assertTrue(unsupported_claim(query, "", "Поэтому невозможно рассчитать ROMI за прошлую неделю."))
        advice = compose_advice(query, ROMI, {})
        for metric in ("расходы", "продажах", "выручке", "прибыли"):
            self.assertIn(metric, advice.answer)
        self.assertIn("пропуски", advice.action)
        self.assertNotIn("окупаемость рассчитана", advice.completion)

    def test_bonus_range_guard_accepts_denials_but_rejects_a_new_universal_recommendation(self):
        query = "Подойдёт ли порог премии 80–85% всем сотрудникам магазина?"
        self.assertFalse(unsupported_claim(query, "", "Порог 80–85% нельзя переносить на всех сотрудников."))
        self.assertFalse(unsupported_claim(query, "", "80–85% — пример для менеджеров по продажам."))
        self.assertTrue(unsupported_claim(query, "", "Порог 80–85% подходит всем сотрудникам."))
        self.assertTrue(unsupported_claim(query, "", "Нельзя переносить 80–85% на всех, но установите 80–85% каждому."))

    def test_known_bonus_example_is_answered_locally_without_spending_api_attempt(self):
        catalog = Mock(is_demo=True, search=Mock(return_value=SearchResult((KPI,))))
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "state.db"
        generator = Mock(public_access=None)
        outbox = Mock()
        bot = Assistant(outbox, catalog, ConversationStore(path), ReminderStore(path), generator=generator)
        bot.message(1, "Подойдёт ли порог премии 80–85% всем сотрудникам магазина, включая кладовщика?")
        generator.generate.assert_not_called()
        self.assertIn("нельзя автоматически", outbox.send.call_args.args[1])
        self.assertTrue(compose_advice("Подойдёт ли порог премии 80–85% всем сотрудникам магазина?", KPI, {}).local_complete)

    def test_mismatched_shop_source_gets_one_clarification_and_preserves_followup(self):
        source = Evidence("Маркетинг магазина одежды", "", "Для магазина одежды подготовьте предложение для клиентов.")
        catalog = Mock(is_demo=True, search=Mock(return_value=SearchResult((source,))))
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "state.db"
        generator = Mock(public_access=None, last_status="unavailable", last_metrics={}, last_clarification="")
        generator.generate.return_value = None
        outbox = Mock()
        store = ConversationStore(path)
        bot = Assistant(outbox, catalog, store, ReminderStore(path), generator=generator)
        bot.message(1, "Хочу больше покупателей для магазина. Бюджет пока не указал")
        generator.generate.assert_not_called()
        self.assertIn("Что продаёт", outbox.send.call_args.args[1])
        self.assertEqual(store.get(1).pending["kind"], "clarify")
        bot.message(1, "Продукты")
        generator.generate.assert_called_once()
        self.assertIn("Продукты", generator.generate.call_args.args[0])
        known = compose_advice("Как привлечь покупателей в магазин продуктов?", source, {})
        self.assertFalse(known.clarification)

    def test_clothing_example_does_not_set_the_stores_assortment(self):
        query = "У меня магазин. Как увеличить повторные продажи?"
        clothing = Evidence("Маркетинг магазина одежды", "", "Настройте акцию для покупателей магазина одежды.")
        profile = {}
        infer_profile(query, profile)
        advice = compose_advice(query, clothing, profile)
        self.assertEqual(profile, {"sector": "Розница"})
        self.assertIn("пример", advice.answer.casefold())
        self.assertTrue(unsupported_claim(query, "", "Начните с покупателей вашего магазина одежды."))
        self.assertFalse(unsupported_claim(query, "", "Как пример, магазин одежды может проверить одну категорию."))

    def test_unknown_budget_never_enforces_zero(self):
        cases = {
            "Как привлечь клиентов?": "unspecified",
            "Мне нужно больше клиентов, бюджет пока не знаю.": "unknown",
            "Как привлечь клиентов без бюджета?": "none",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(budget_state(query), expected)
                self.assertEqual(unsupported_claim(query, "", "Используйте только бесплатные каналы."),
                                 expected != "none")
        self.assertFalse(unsupported_claim("Как привлечь клиентов?", "",
                                           "Если бюджета нет, можно рассмотреть бесплатные каналы."))

    def test_optional_mystery_shopper_offer_is_not_a_guarantee(self):
        self.assertTrue(unsupported_claim("Нужен ли тайный покупатель магазину?", "",
                                          "Закажите бесплатную проверку тайным покупателем."))
        self.assertTrue(unsupported_claim("Как увеличить повторные продажи?", "",
                                          "Запустите бесплатную программу лояльности."))
        self.assertFalse(unsupported_claim("Нужен ли тайный покупатель магазину?", "",
                                           "Проверьте, предлагает ли исполнитель пробную проверку, и сравните условия."))

    def test_document_metadata_cannot_be_presented_as_reading_the_file(self):
        metadata = Evidence("Шаблон для расчёта", "", "Описание учебного файла.",
                            kind="document", content_scope="document_description")
        advice = compose_advice("Что есть в шаблоне?", metadata, {})
        self.assertIn("описание", advice.answer)
        self.assertIn("карточку", advice.action)
        self.assertTrue(unsupported_scope_claim(("document_description",), "Я открыл шаблон и посчитал результат."))
        self.assertFalse(unsupported_scope_claim(("document_description",), "По карточке можно найти файл."))

    def test_generic_tax_question_does_not_choose_a_regime(self):
        catalog = self.make_catalog(Evidence("Как платить налоги на УСН", "", "Инструкция по налогам на УСН."))
        self.assertTrue(tax_regime_needed("Как платить налоги на маркетплейсе?"))
        result = catalog.search("Как платить налоги на маркетплейсе?")
        self.assertTrue(result.needs_clarification)
        self.assertFalse(result.evidence)
        self.assertFalse(tax_regime_needed("Как платить налоги на УСН?"))

    def test_shared_prompt_applies_adaptation_in_both_modes(self):
        for prompt in (SYSTEM_PROMPT, COMPACT_PROMPT):
            with self.subTest(prompt=prompt[-60:]):
                self.assertNotIn("4–6", prompt)
                for rule in ("неизвестный бюджет", "80–85%", "description", "40% = 0,4",
                             "Число пунктов определяется", "Неучтённые заявки"):
                    self.assertIn(rule, prompt)

    def test_streamed_unsupported_claim_is_not_shown(self):
        response = Mock(headers={"Content-Type": "text/event-stream"})
        answer = "Поэтому невозможно рассчитать ROMI за прошлую неделю."
        document = {"status": "answered", "source_index": 0, "source_indices": [0],
                    "quote": ROMI.passage, "answer": answer,
                    "action": "Проверьте рекламу.", "completion": "Реклама проверена."}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.__iter__ = Mock(return_value=iter(
            [
                b"data: " + json.dumps({"type": "response.output_text.delta",
                                       "delta": json.dumps(document, ensure_ascii=False)}).encode(),
                b"data: " + json.dumps({"type": "response.completed", "response": {
                    "status": "completed", "output": [{"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": json.dumps(document)}]}],
                }}).encode(),
            ]
        ))
        generator = ResponseGenerator("xai", "synthetic-token", "synthetic-model")
        generator.opener = Mock()
        generator.opener.open.return_value = response
        preview = Mock()
        query = "Как оценить ROMI, если заявки не записывали?"
        self.assertIsNone(generator.generate(query, {}, (ROMI,), is_demo=True, on_preview=preview))
        preview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
