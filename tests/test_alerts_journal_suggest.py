import os
import unittest

from _util import SkillCase


class TestAlerts(SkillCase):
    def test_status(self):
        rc, out = self.run_skill("alerts", "status", now="2026-11-07 20:00")
        self.assertEqual(rc, 1)
        self.assertIn("Muhurat", out)

    def test_plan_alerts_ring_once(self):
        self.run_skill("entry", "check", "DEMOBRK", now="2026-10-21 10:06")
        self.run_skill("exit", "plan", now="2026-10-21 10:06")
        rc, out = self.run_skill("alerts", "add", "--from-plan", now="2026-10-21 10:06")
        self.assertEqual(out.count("Set #"), 3)
        rc, out = self.run_skill("alerts", "check", now="2026-10-21 10:30")
        self.assertEqual(rc, 1)
        rc, out = self.run_skill("alerts", "check", now="2026-10-21 13:00")
        self.assertIn("RING #2", out)                               # the target
        rc, out = self.run_skill("alerts", "check", now="2026-10-21 13:05")
        self.assertNotIn("RING #2", out)                            # rings once
        rc, out = self.run_skill("alerts", "check", now="2026-10-21 15:11")
        self.assertIn("RING #3", out)                               # flat by 15:10
        rc, out = self.run_skill("alerts", "list")
        self.assertIn("#1   active", out)

    def test_manual_and_remove(self):
        rc, out = self.run_skill("alerts", "add", "DEMOFLAT", "--below", "470", "--note", "support", now="2026-10-21 10:00")
        self.assertIn("Set #1", out)
        self.run_skill("alerts", "remove", "1")
        rc, out = self.run_skill("alerts", "list")
        self.assertIn("No alerts", out)


class TestJournal(SkillCase):
    def suggestion(self):
        self.run_skill("entry", "check", "DEMOBRK", now="2026-10-21 10:06")
        self.run_skill("exit", "plan", now="2026-10-21 10:06")
        self.run_skill("tm", "run", "--replays", "2000", now="2026-10-21 10:06")
        self.run_skill("budget", now="2026-10-21 10:06")

    def test_log_take_review_stats(self):
        self.suggestion()
        rc, out = self.run_skill("journal", "log", now="2026-10-21 10:07")
        self.assertIn("Logged J1", out)
        rc, out = self.run_skill("journal", "log", now="2026-10-21 10:07")
        self.assertIn("Already logged", out)
        self.run_skill("journal", "take", "J1", "--price", "1006.5", now="2026-10-21 10:08")
        rc, out = self.run_skill("journal", "review", now="2026-10-21 11:00")
        self.assertIn("still running", out)
        rc, out = self.run_skill("journal", "review", now="2026-10-21 15:20")
        self.assertIn("target", out)
        e = self.read("journal.json")["entries"][0]
        self.assertEqual(e["outcome"], "target")
        self.assertLess(e["r_net"], e["r_gross"])
        self.assertIsNotNone(e["pnl"])
        rc, out = self.run_skill("journal", "stats")
        self.assertIn("Calibration over 1", out)
        self.assertIn("Fewer than 30", out)
        rc, out = self.run_skill("journal", "close", "J1", "--price", "1018", now="2026-10-21 12:10")
        self.assertIn("after charges", out)
        self.assertEqual(self.read("journal.json")["entries"][0]["pnl_basis"], "your exit")

    def test_not_filled(self):
        self.run_skill("entry", "check", "DEMOFLAT", now="2026-10-21 10:06")
        self.run_skill("exit", "plan", now="2026-10-21 10:06")
        self.run_skill("journal", "log", now="2026-10-21 10:07")
        rc, out = self.run_skill("journal", "review", now="2026-10-21 15:20")
        self.assertIn("not filled", out)

    def test_close_requires_take(self):
        self.suggestion()
        self.run_skill("journal", "log", now="2026-10-21 10:07")
        rc, out = self.run_skill("journal", "close", "J1", "--price", "1010")
        self.assertIn("take J1 first", out)


class TestSuggest(SkillCase):
    def test_intraday_card(self):
        rc, out = self.run_skill("suggest", "run", "DEMOBRK", "--replays", "2000", now="2026-10-21 10:06")
        self.assertEqual(rc, 0, out)
        for part in ("SUGGESTION CARD: DEMOBRK", "Plan", "Size", "Odds", "history:", "News", "Checks", "Evidence", "Bottom line"):
            self.assertIn(part, out)
        self.assertNotIn("buy it", out.lower())
        self.assertTrue(os.path.exists(os.path.join(self.tdir, "suggestion.md")))

    def test_swing_card(self):
        self.run_skill("scan", "swing", "--symbols", "DEMODIP", now="2026-10-22 08:30")
        rc, out = self.run_skill("suggest", "run", "DEMODIP", "--mode", "swing", "--replays", "2000", now="2026-10-22 08:30")
        self.assertEqual(rc, 0, out)
        self.assertIn("swing: oversold bounce", out)
        self.assertIn("out after 10 sessions", out)

    def test_stops_when_closed(self):
        rc, out = self.run_skill("suggest", "run", "DEMOBRK", now="2026-10-20 10:06")
        self.assertEqual(rc, 1)
        self.assertIn("No suggestion", out)


if __name__ == "__main__":
    unittest.main()
