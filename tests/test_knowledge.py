import json
import tempfile
import unittest
from pathlib import Path

from assistant_bot.knowledge import DEMO_CATALOG, Catalog
from scripts.catalog_build import TABLES, extract_records, from_copy


class CatalogSearchTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog(DEMO_CATALOG)

    def test_launch_demo_returns_grounded_advice(self):
        result = self.catalog.search("Как проверить спрос, если я планирую открыть кофейню?")
        self.assertTrue(self.catalog.is_demo)
        self.assertFalse(result.needs_clarification)
        self.assertIn("до открытия", result.evidence[0].title)
        self.assertIn("Посчитайте прохожих", result.evidence[0].passage)
        self.assertEqual(result.evidence[0].url, "")

    def test_same_query_changes_with_business_stage(self):
        query = "Как проверить спрос кофейни?"
        launch = self.catalog.search(query, {"stage": "готовится к запуску"})
        operate = self.catalog.search(query, {"stage": "уже работает"})
        self.assertIn("до открытия", launch.evidence[0].title)
        self.assertIn("новое меню", operate.evidence[0].title)

    def test_vague_and_out_of_catalog_are_distinct_outcomes(self):
        self.assertTrue(self.catalog.search("маркетинг").needs_clarification)
        missing = self.catalog.search("Сколько лун у Юпитера?")
        self.assertFalse(missing.needs_clarification)
        self.assertEqual(missing.evidence, ())
        self.assertEqual(missing.recommendations, ())

    def test_paid_card_exposes_only_metadata(self):
        result = self.catalog.search("Как вести налоговый учёт на УСН при продажах на маркетплейсе?")
        self.assertEqual(result.evidence, ())
        self.assertEqual(len(result.recommendations), 1)
        self.assertTrue(result.recommendations[0].is_paid)
        self.assertEqual(result.recommendations[0].price_display, "2 500 ₽")

    def test_paid_passages_are_ignored_even_if_present_in_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.jsonl"
            rows = [
                {"format": 1, "mode": "demo"},
                {"title": "Курс по логистике", "url": "", "kind": "course", "price": 100,
                 "passages": [{"text": "Секретная методика таможенного оформления."}]},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            result = Catalog(path).search("Секретная методика таможенного оформления")
            self.assertEqual(result.evidence, ())
            self.assertEqual(result.recommendations, ())


class CatalogImportTests(unittest.TestCase):
    def test_pg_copy_decode(self):
        self.assertIsNone(from_copy(r"\N"))
        self.assertEqual(from_copy(r"tab\tline\nslash\\end"), "tab\tline\nslash\\end")

    def test_only_public_article_blocks_and_course_cards_are_exported(self):
        tables = {name: [] for name in TABLES}
        tables["Statuses"] = [{"Id": "published", "Title": "Опубликовано"}]
        article = {
            "Id": "a", "Title": "Памятка для предпринимателя", "Url": "primer",
            "StatusId": "published", "Published": "2026-01-01", "Description": None, "CardDescription": None,
            "SubjectId": "sub", "IsPublic": "t", "IsDeleted": "f", "IsClosed": "f", "IsLesson": "f", "NoIndex": "f",
        }
        tables["Articles"] = [article, {**article, "Id": "hidden", "IsClosed": "t"}]
        tables["ArticleEmbedItems"] = [
            {"ArticleId": "a", "Type": "0", "Order": "0", "Params": None,
             "HtmlContent": "<h2>Подготовка</h2><p>Соберите список задач перед открытием магазина.</p>"
                            "<script>Приватный идентификатор.</script>"},
            {"ArticleId": "a", "Type": "4", "Order": "1", "HtmlContent": None,
             "Params": json.dumps({"title": "План", "items": ["Оцените расходы по каждой статье бюджета."]})},
            {"ArticleId": "hidden", "Type": "0", "Order": "0", "Params": None,
             "HtmlContent": "<p>Этот закрытый текст нельзя включать в результат.</p>"},
        ]
        course = {**article, "Id": "c", "Title": "Углублённый курс", "Url": "paid", "Cost": "100"}
        tables["Courses"] = [course, {**course, "Id": "draft", "StatusId": "draft"}]
        article_record, course_record = extract_records(tables)
        passages = " ".join(item["text"] for item in article_record["passages"])
        self.assertIn("Соберите список", passages)
        self.assertIn("Оцените расходы", passages)
        self.assertNotIn("Приватный идентификатор", passages)
        self.assertNotIn("закрытый текст", passages)
        self.assertEqual(course_record["price"], 100)
        self.assertEqual(course_record["url"], "https://kurs.alfabank.ru/courses/paid/")
        self.assertNotIn("passages", course_record)
