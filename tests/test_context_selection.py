import json
import tempfile
import unittest
from pathlib import Path

from assistant_bot.knowledge import Catalog


class ContextSelectionTests(unittest.TestCase):
    def catalog(self, rows):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "catalog.jsonl"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in
                                  [{"format": 1, "mode": "demo"}, *rows]), encoding="utf-8")
        return Catalog(path)

    def row(self, title, passages, kind="article"):
        return {"title": title, "url": "", "kind": kind, "price": 0, "passages": passages}

    def test_exact_definition_does_not_need_three_articles(self):
        catalog = self.catalog([
            self.row("NPS", [{"text": "NPS — индекс лояльности. NPS = доля промоутеров минус доля критиков."}], "glossary"),
            self.row("Опросы покупателей", [{"text": "Опрос NPS помогает оценивать сервис и искать проблемы в обслуживании."}]),
        ])
        result = catalog.search("Что такое NPS простыми словами?")
        self.assertEqual(len(result.evidence), 1)
        self.assertIn("промоутеров", result.evidence[0].passage)

    def test_all_numbered_steps_survive_frequent_introductory_keywords(self):
        intro = "Бизнес-план помогает бизнесу. " * 40
        passages = [{"text": intro, "heading": "Бизнес-план"}]
        passages += [{"heading": f"Шаг {i}: {name}", "text": text} for i, name, text in [
            (1, "Рынок", "Изучите конкурентов и потребности будущих клиентов."),
            (2, "Цели", "Поставьте измеримые цели и сроки их достижения."),
            (3, "Финансы", "Посчитайте расходы, доходы и точку безубыточности."),
        ]]
        evidence = self.catalog([self.row("Как составить бизнес-план", passages)]).search("Как составить бизнес-план?").evidence[0]
        for word in ("конкурентов", "измеримые", "безубыточности"):
            self.assertIn(word, evidence.passage)
        self.assertLessEqual(len(evidence.passage), 3600)

    def test_definition_preserves_formula_even_without_topic_in_formula_text(self):
        passages = [{"text": "NPS — индекс лояльности и готовности рекомендовать компанию."}]
        passages += [{"text": "NPS помогает понять клиентов. " * 22, "heading": "Зачем нужен NPS"} for _ in range(8)]
        passages += [{"text": "Индекс = доля промоутеров − доля критиков.", "heading": "Формула расчёта"},
                     {"text": "Нейтралы учитываются в общем числе ответов.", "heading": "Формула расчёта"}]
        evidence = self.catalog([self.row("NPS", passages, "glossary")]).search("Что такое NPS?").evidence[0]
        self.assertIn("доля промоутеров", evidence.passage)
        self.assertIn("Нейтралы", evidence.passage)

    def test_oversized_best_chunk_keeps_formula_and_nearby_condition(self):
        filler = "Здесь описывается история проекта и его развитие. " * 100
        text = filler + "\nСтоимость заказа = расходы на рекламу / число заказов.\nСравнивайте одинаковые периоды и одинаковые цели."
        catalog = self.catalog([self.row("Стоимость заказа", [{"text": text}])])
        passage = catalog.search("Стоимость заказа").evidence[0].passage
        self.assertIn("/ число заказов", passage)
        self.assertIn("одинаковые периоды", passage)
        self.assertLessEqual(len(passage), 3600)

    def test_related_distant_section_is_kept_in_source_order(self):
        passages = [{"text": "Возврат товара: сохраните заявление и сведения о покупке."},
                    {"text": "История компании и её маркетинговые планы." * 100},
                    {"text": "Возврат товара оформляется актом с датой и суммой возврата."}]
        passage = self.catalog([self.row("Возврат товара", passages)]).search("Возврат товара").evidence[0].passage
        self.assertIn("сохраните заявление", passage)
        self.assertIn("датой и суммой", passage)
        self.assertLess(passage.index("заявление"), passage.index("датой"))


if __name__ == "__main__":
    unittest.main()
