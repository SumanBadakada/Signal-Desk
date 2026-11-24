import unittest
from unittest.mock import patch

from battlecards import supplied
from desk_chat import answer


class ChatTest(unittest.TestCase):
    def test_comparison_and_followup(self):
        cards = supplied()
        initial = answer("Compare Tableau and Power BI on pricing", cards)
        self.assertEqual(
            {s["company"] for s in initial["sources"]}, {"Power BI", "Tableau"}
        )
        self.assertTrue(all(s["source"] for s in initial["sources"]))
        follow = answer("What about their G2 ratings?", cards, initial["context"])
        self.assertEqual(
            {s["company"] for s in follow["sources"]}, {"Power BI", "Tableau"}
        )
        self.assertIn("G2 rating", [s["label"] for s in follow["sources"]])

    def test_only_documented_support(self):
        reply = answer("Which products support custom transformations?", supplied())
        self.assertGreater(len(reply["sources"]), 0)
        for s in reply["sources"]:
            self.assertEqual(s["label"], "Custom Transformations")
            self.assertTrue(s["text"].startswith("✓"))

    def test_unsupported_question(self):
        self.assertFalse(answer("Does Tableau sell bicycles?", supplied())["sources"])

    def test_live_answer_uses_monitor_timestamp(self):
        with patch("desk_chat.init_monitor"), patch(
            "desk_chat.history", return_value=[]
        ), patch(
            "desk_chat.last_success", return_value={"summary": "SDK · 1.0", "ts": 1000}
        ):
            reply = answer("Latest Fivetran version?", supplied())
        source = reply["sources"][0]
        self.assertTrue(source["url"].startswith("https://"))
        self.assertIn("UTC", source["date"])
        self.assertEqual(source["text"], "SDK · 1.0")
