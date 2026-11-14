import json
import tempfile
import unittest
from pathlib import Path

from assistant_bot.knowledge import Catalog
from scripts.catalog_build import TABLES, extract_records, _passages


class ExpandedCatalogTest(unittest.TestCase):
    def catalog(self, rows):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "catalog.jsonl"
        path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in
                                   [{"format": 1, "mode": "demo"}, *rows]), encoding="utf-8")
        return Catalog(path)

    def row(self, title, text, kind="article", **kwargs):
        return {"title": title, "url": "", "kind": kind, "price": 0,
                "passages": [{"text": text, "heading": title}], **kwargs}

    def test_compound_topic_is_not_replaced_with_manufacturing_plan(self):
        cat = self.catalog([
            self.row("Производственный план", "План производства описывает план загрузки цеха и план закупок."),
            self.row("Как составить бизнес-план", "Бизнес-план содержит описание продукта, анализ рынка и финансовые расчёты."),
        ])
        self.assertIn("бизнес-план", cat.search("Как сделать бизнес план?").evidence[0].title)

    def test_inflections_and_topic_specificity(self):
        cat = self.catalog([
            self.row("Как удержать клиентов", "Сотрудники общаются с клиентами. Удержать клиентов помогает сервис."),
            self.row("Почему сотрудники увольняются", "Увольняться сотрудники могут из-за условий труда и отсутствия развития."),
        ])
        self.assertIn("увольняются", cat.search("Как удержать сотрудников?").evidence[0].title)

    def test_platform_is_not_replaced_with_another_marketplace(self):
        cat = self.catalog([
            self.row("Реклама на Ozon", "Продвижение магазина и настройка рекламных объявлений на Ozon."),
            self.row("Реклама в Яндекс Директе", "Настройте объявления в Яндекс Директе для выбранной аудитории."),
        ])
        results = cat.search("Как настроить рекламу в Яндекс Директе?")
        self.assertTrue(results.evidence)
        self.assertTrue(all("Ozon" not in e.title for e in results.evidence))

    def test_glossary_and_document_text_are_searchable_but_subscription_is_not(self):
        cat = self.catalog([
            self.row("Франшиза", "Франшиза — право использовать бизнес-модель и бренд по договору.", "glossary"),
            self.row("Соглашение о неразглашении", "Соглашение определяет конфиденциальные сведения и обязанности сторон.", "document"),
            self.row("Закрытая методика", "Уникальная секретная методика логистического планирования.",
                     "lesson", access_type="subscription"),
        ])
        self.assertEqual(cat.search("Что такое франшиза?").evidence[0].kind, "glossary")
        self.assertFalse(cat.search("Что такое франшиза?").needs_clarification)
        self.assertEqual(cat.search("Соглашение о неразглашении").evidence[0].kind, "document")
        self.assertEqual(cat.search("Уникальная секретная методика логистического планирования").evidence, ())

    def test_source_context_keeps_neighboring_definition_and_formula(self):
        row = self.row("Стоимость заказа", "Общее описание результата рекламной кампании.")
        row["passages"] += [{"text": "Стоимость заказа равна расходам на рекламу, делённым на число заказов."},
                            {"text": "Для сравнения выбирайте одинаковый период и цель кампании."}]
        result = self.catalog([row]).search("Стоимость заказа")
        self.assertIn("делённым", result.evidence[0].passage)
        self.assertIn("одинаковый период", result.evidence[0].passage)

    def test_long_source_blocks_are_preserved(self):
        text = "длинный фрагмент " * 150
        parts = _passages([{"Type": "0", "Order": "0", "HtmlContent": "<p>" + text + "</p>"}])
        self.assertGreater(len(parts), 1)
        self.assertEqual(" ".join(p["text"] for p in parts), text.strip())

    def test_import_excludes_paid_and_subscription_lessons_and_hidden_documents(self):
        tables = {name: [] for name in TABLES}
        tables["Statuses"] = [{"Id": "pub", "Title": "Опубликовано"}]
        common = {"StatusId": "pub", "Published": "2026-01-01", "IsDeleted": "f",
                  "NoIndex": "f", "IsClosed": "f"}
        tables["Articles"] = [{**common, "Id": "article", "Title": "Открытая статья", "Url": "article",
                               "IsPublic": "t", "IsLesson": "f", "SubjectId": None,
                               "CardDescription": None, "Description": None}]
        tables["ArticleEmbedItems"] = [{"ArticleId": "article", "Type": "0", "Order": "0",
                                       "HtmlContent": "<p>Открытая инструкция для решения задачи предпринимателя.</p>"}]
        tables["Courses"] = [{**common, "Id": key, "Title": key, "Url": key, "Cost": price, "Description": "Описание"}
                             for key, price in [("free", "0"), ("paid", "100"), ("subscription", "0")]]
        tables["CourseSubscriptions"] = [{"CourseId": "subscription", "SubscriptionId": "s"}]
        for course in ("free", "paid", "subscription"):
            tables["Lessons"].append({"Id": course, "Title": course, "Description": "", "IsDeleted": "f", "IsGroup": "f"})
            tables["CourseLessonBindings"].append({"CourseId": course, "LessonId": course,
                                                   "IsDeleted": "f", "IsClosed": "f", "ClosedUntil": None})
            tables["LessonEmbedItems"].append({"LessonId": course, "Type": "0", "Order": "0",
                                              "HtmlContent": "<p>Содержимое урока, которое нельзя раскрывать без проверки доступа.</p>"})
        tables["Documents"] = [{**common, "Id": "hidden", "Title": "Скрытый документ", "Url": "hidden", "NoIndex": "t"},
                               {**common, "Id": "public-doc", "Title": "Шаблон документа", "Url": "public-doc",
                                "Published": None, "Description": "Описание открытой карточки, а не содержимое скачанного файла."}]
        records = extract_records(tables)
        self.assertEqual([r["id"] for r in records if r["kind"] == "lesson"], ["free"])
        self.assertFalse(any(r["id"] == "hidden" for r in records))
        self.assertEqual(next(r for r in records if r["id"] == "public-doc")["content_scope"], "document_description")
        subscription = next(r for r in records if r["id"] == "subscription")
        self.assertEqual(subscription["access_type"], "subscription")
