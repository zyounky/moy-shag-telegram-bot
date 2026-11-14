import unittest

from assistant_bot.advice import catalog_context, infer_profile
from assistant_bot.knowledge import Catalog, DEMO_CATALOG
from assistant_bot.query_language import russian_search_query


class RussianSearchQueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog(DEMO_CATALOG)

    def test_english_examples_retrieve_expected_demo_articles(self):
        examples = {
            "How can I test demand before opening a coffee shop?":
                "Как проверить спрос до открытия кофейни",
            "How do I measure ad performance for my coffee shop?":
                "Как измерить результат рекламы небольшой кофейни",
            "How do I reconcile Ozon sales with its report?":
                "Как сверить продажи с отчётом маркетплейса Ozon",
        }
        for english, expected_title in examples.items():
            with self.subTest(english=english):
                query = russian_search_query(english)
                profile = {}
                infer_profile(query, profile)
                result = self.catalog.search(query, catalog_context(profile))
                self.assertTrue(result.evidence, query)
                self.assertEqual(result.evidence[0].title, expected_title)

    def test_supported_stage_and_sector_are_inferred(self):
        profile = {}
        prefer = russian_search_query("How can I test demand before opening a coffee shop?")
        infer_profile(prefer, profile)
        self.assertEqual(profile, {"stage": "launch", "sector": "Кофейня"})

    def test_russian_input_is_preserved_verbatim(self):
        question = "Как сверить продажи с отчётом Ozon?"
        self.assertEqual(russian_search_query(question), question)

    def test_unrecognized_english_is_preserved_verbatim(self):
        question = "How do photons entangle in quantum mechanics?"
        self.assertEqual(russian_search_query(question), question)

    def test_unsupported_topic_is_retained_when_some_words_match(self):
        question = russian_search_query("How can I observe quantum tunneling in a coffee shop?")
        self.assertIn("observe quantum tunneling", question)
        self.assertIn("кофейня", question)
        result = self.catalog.search(question)
        self.assertFalse(result.evidence)


if __name__ == "__main__":
    unittest.main()
