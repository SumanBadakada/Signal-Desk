import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import app
import product_monitor as monitor


class MonitorTest(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(app, "DATA", folder))
        self.enterContext(patch.object(monitor, "DATA", folder))
        monitor.init_monitor()
        self.feed = monitor.feeds()["Fivetran"][0]

    def test_overlapping_checks_share_a_sample(self):
        with patch.object(monitor, "observe", return_value="Release 1") as observe:
            with ThreadPoolExecutor(4) as pool:
                list(
                    pool.map(
                        lambda _: monitor.check_source("Fivetran", self.feed, 30),
                        range(4),
                    )
                )
        observe.assert_called_once()
        self.assertEqual(len(monitor.history("Fivetran")), 1)

    def test_failed_check_preserves_last_value_and_change_baseline(self):
        observations = ["Release 1", OSError("unavailable"), "Release 1", "Release 2"]
        with patch.object(monitor, "observe", side_effect=observations):
            for _ in observations:
                monitor.check_source("Fivetran", self.feed, 0)
        statuses = [r["outcome"] for r in reversed(monitor.history("Fivetran"))]
        self.assertEqual(statuses, ["Baseline", "Unavailable", "Unchanged", "Changed"])
        self.assertEqual(monitor.last_success(self.feed["url"])["summary"], "Release 2")
