import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

from assistant_bot.budget import reserve_request
from assistant_bot.knowledge import Evidence
from assistant_bot.llm import ResponseGenerator


class PersistentBudgetTests(unittest.TestCase):
    def test_concurrent_process_connections_do_not_exceed_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.db"
            with ThreadPoolExecutor(max_workers=4) as executor:
                accepted = list(executor.map(lambda _: reserve_request(path, "same", 5), range(16)))
            self.assertEqual(sum(accepted), 5)
            self.assertFalse(reserve_request(path, "same", 5))
            self.assertTrue(reserve_request(path, "new-authorized-window", 5))

    def test_failed_request_remains_spent_after_generator_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.db"
            source = Evidence("Реклама", "", "Расходы на рекламу разделите на число заказов за одинаковый период.")
            first = ResponseGenerator("xai", "test", "model", max_requests=1, budget_path=path)
            first.opener = Mock()
            first.opener.open.return_value = BytesIO(b"invalid response")
            self.assertIsNone(first.generate("Реклама", {}, (source,), is_demo=True))
            second = ResponseGenerator("xai", "test", "model", max_requests=1, budget_path=path)
            second.opener = Mock()
            self.assertIsNone(second.generate("Реклама", {}, (source,), is_demo=True))
            self.assertEqual(second.last_status, "budget")
            second.opener.open.assert_not_called()
