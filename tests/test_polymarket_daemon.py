import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import polymarket_ws_daemon as daemon


class MonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = daemon.Monitor()
        self.meta = {
            "market_id": "market-1",
            "slug": "slug-1",
            "label": "label-1",
            "market_title": "title-1",
            "url": "https://example.com/market-1",
            "yes_token_id": "yes-token",
            "no_token_id": "no-token",
        }
        self.monitor.market_map = {
            "yes-token": {**self.meta, "token_id": "yes-token"},
            "no-token": {**self.meta, "token_id": "no-token"},
        }

    def test_process_trade_normalizes_no_token_price(self) -> None:
        with patch.object(self.monitor, "save_state", return_value=None):
            self.monitor.process_trade("no-token", 0.30, 150000)

        item = self.monitor.state["markets"]["market-1"]
        self.assertAlmostEqual(item["last_yes_price"], 0.70)
        self.assertAlmostEqual(item["last_no_price"], 0.30)
        self.assertEqual(item["history"][-1]["volume_delta"], 150000)

    def test_refresh_watchlist_reports_subscription_change(self) -> None:
        watchlist = [{"event_id": "slug-1", "label": "label-1"}]
        fetched = [
            {
                "market_id": "market-1",
                "slug": "slug-1",
                "label": "label-1",
                "market_title": "title-1",
                "url": "https://example.com/market-1",
                "token_ids": ["yes-token", "no-token"],
                "yes_token_id": "yes-token",
                "no_token_id": "no-token",
            }
        ]
        self.monitor.market_map = {}

        with patch.object(self.monitor, "save_state", return_value=None), \
             patch.object(daemon, "load_watchlist", return_value=watchlist), \
             patch.object(daemon, "fetch_markets_for_slug", return_value=fetched), \
             patch.object(daemon.time, "time", side_effect=[1000.0, 1000.0, 1400.0, 1400.0]):
            self.assertTrue(self.monitor.refresh_watchlist())
            self.assertFalse(self.monitor.refresh_watchlist())


if __name__ == "__main__":
    unittest.main()
