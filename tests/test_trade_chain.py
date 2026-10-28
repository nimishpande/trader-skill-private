import json
import unittest

from _util import SkillCase

import indmkt as m


class TestEntry(SkillCase):
    def state(self, now, *extra):
        rc, out = self.run_skill("entry", "check", "DEMOBRK", *extra, now=now)
        return self.read("entry.json")["state"], out

    def test_states_through_the_morning(self):
        self.assertEqual(self.state("2026-10-21 09:10")[0], "not_open")
        self.assertEqual(self.state("2026-10-21 09:25")[0], "range_forming")
        self.assertEqual(self.state("2026-10-21 09:35")[0], "waiting")
        self.assertEqual(self.state("2026-10-21 10:02")[0], "waiting")
        st, out = self.state("2026-10-21 10:06")
        self.assertEqual(st, "triggered")
        doc = self.read("entry.json")
        self.assertEqual(doc["trigger_time"], "10:05")
        self.assertFalse(doc["stale"])
        st, out = self.state("2026-10-21 11:30")
        self.assertTrue(self.read("entry.json")["stale"])
        self.assertIn("STALE", out)
        self.assertEqual(self.state("2026-10-20 10:00")[0], "closed")

    def test_volume_filter(self):
        st, out = self.state("2026-10-21 10:31", "--min-volume-ratio", "5")
        self.assertEqual(st, "waiting")
        self.assertIn("on volume", out)

    def test_expired_and_unusable(self):
        rc, out = self.run_skill("entry", "check", "DEMOFLAT", now="2026-10-21 14:31")
        self.assertEqual(self.read("entry.json")["state"], "expired")
        rc, out = self.run_skill("entry", "check", "DEMOGAP", now="2026-10-21 10:31")
        self.assertEqual(self.read("entry.json")["state"], "unusable")

    def test_watch_replays_the_clock(self):
        rc, out = self.run_skill("entry", "watch", "DEMOBRK", "--every", "300", now="2026-10-21 09:50")
        lines = out.strip().splitlines()
        self.assertEqual(rc, 0)
        self.assertEqual(len(lines), 4)
        self.assertIn("TRIGGERED at 10:05", lines[-1])

    def test_swing_from_watchlist(self):
        self.run_skill("scan", "swing", "--symbols", "DEMOBRK,DEMOFLAG", now="2026-10-22 08:30")
        rc, out = self.run_skill("entry", "check", "DEMOBRK", "--setup", "swing", now="2026-10-22 08:30")
        doc = self.read("entry.json")
        self.assertEqual(doc["state"], "not_open")
        self.assertEqual(doc["signal_date"], "2026-10-21")
        self.assertGreater(doc["atr14"], 0)

    def test_swing_needs_a_level(self):
        rc, out = self.run_skill("entry", "check", "DEMOBRK", "--setup", "swing", now="2026-10-22 08:30")
        self.assertIn("Run the swing scan first", out)


class TestExit(SkillCase):
    def setUp(self):
        super().setUp()
        self.run_skill("entry", "check", "DEMOBRK", now="2026-10-21 10:06")

    def test_default_intraday_plan(self):
        rc, out = self.run_skill("exit", "plan", now="2026-10-21 10:06")
        self.assertEqual(rc, 0, out)
        p = self.read("plan.json")
        e = self.read("entry.json")
        self.assertEqual(p["entry"], e["entry"])
        self.assertLessEqual(p["stop"], (e["or_high"] + e["or_low"]) / 2)
        self.assertAlmostEqual(p["stop"], m.floor_tick(p["stop"]))
        self.assertAlmostEqual(p["target"], m.floor_tick(p["entry"] + 2 * p["risk_per_share"]))
        self.assertLessEqual(p["r_multiple"], 2.0)
        self.assertEqual(p["flat_by"], "15:10")
        self.assertEqual(p["product"], "intraday")

    def test_refusals(self):
        rc, out = self.run_skill("exit", "plan", "--stop", "pct:0.05", now="2026-10-21 10:06")
        self.assertEqual(rc, 1)
        self.assertIn("noise", out)
        rc, out = self.run_skill("exit", "plan", "--stop", "price:2000", now="2026-10-21 10:06")
        self.assertIn("at or above the entry", out)
        rc, out = self.run_skill("exit", "plan", "--stop", "banana", now="2026-10-21 10:06")
        self.assertIn("not one of", out)

    def test_manual(self):
        rc, out = self.run_skill("exit", "plan", "--entry", "2914.5", "--symbol", "RELIANCE", "--mode", "swing",
                                 "--stop", "pct:3", "--target", "price:3050", offline=False)
        self.assertEqual(rc, 0, out)
        p = self.read("plan.json")
        self.assertEqual(p["product"], "delivery")
        self.assertEqual(p["stop"], 2827.0)
        self.assertEqual(p["target"], 3050.0)


class TestTimeMachine(SkillCase):
    def plan(self):
        self.run_skill("entry", "check", "DEMOBRK", now="2026-10-21 10:06")
        self.run_skill("exit", "plan", now="2026-10-21 10:06")

    def test_intraday_deterministic(self):
        self.plan()
        rc, out1 = self.run_skill("tm", "run", "--replays", "3000", now="2026-10-21 10:06")
        self.assertEqual(rc, 0, out1)
        d1 = self.read("odds.json")
        rc, out2 = self.run_skill("tm", "run", "--replays", "3000", now="2026-10-21 10:06")
        self.assertEqual(out1, out2)
        s = d1["simulation"]
        self.assertAlmostEqual(s["p_target"] + s["p_stop"] + s["p_neither"], 1.0, places=3)
        self.assertEqual(s["sessions"], 60)
        h = d1["history"]
        self.assertGreater(h["n"], 15)
        lo, hi = h["ci95_avg_r_net"]
        self.assertLessEqual(lo, h["avg_r_net"])
        self.assertGreaterEqual(hi, h["avg_r_net"])
        self.assertLess(h["avg_r_net"], h["avg_r_gross"])          # charges always cost something
        self.assertIn(d1["evidence"], ("weak", "mixed", "supportive"))

    def test_seed_changes_simulation(self):
        self.plan()
        self.run_skill("tm", "run", "--replays", "2000", "--seed", "1", now="2026-10-21 10:06")
        a = self.read("odds.json")["simulation"]["p_target"]
        self.run_skill("tm", "run", "--replays", "2000", "--seed", "2", now="2026-10-21 10:06")
        b = self.read("odds.json")["simulation"]["p_target"]
        self.assertNotEqual(a, b)

    def test_swing(self):
        self.run_skill("scan", "swing", "--symbols", "DEMOFLAG", now="2026-10-22 08:30")
        self.run_skill("entry", "check", "DEMOFLAG", "--setup", "swing", now="2026-10-22 08:30")
        rc, out = self.run_skill("exit", "plan", now="2026-10-22 08:30")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_skill("tm", "run", "--replays", "2000", now="2026-10-22 08:30")
        self.assertEqual(rc, 0, out)
        d = self.read("odds.json")
        self.assertEqual(d["mode"], "swing")
        self.assertGreater(d["history"]["n"], 0)
        for t in d["history"]["trades"]:
            self.assertIn(t["outcome"], ("target", "stop", "time"))
            self.assertLessEqual(t["entry_date"], t["exit_date"])

    def test_needs_a_plan(self):
        rc, out = self.run_skill("tm", "run", now="2026-10-21 10:06")
        self.assertIn("Run /trader-exit first", out)


class TestBudget(SkillCase):
    def chain(self):
        self.run_skill("scan", "universe")
        self.run_skill("entry", "check", "DEMOBRK", now="2026-10-21 10:06")
        self.run_skill("exit", "plan", now="2026-10-21 10:06")

    def test_size_fits_loss_with_charges(self):
        self.chain()
        rc, out = self.run_skill("budget", "--max-loss", "100", "--capital", "1000000", now="2026-10-21 10:06")
        self.assertEqual(rc, 0, out)
        d = self.read("sizing.json")
        p = self.read("plan.json")
        self.assertLessEqual(d["loss_at_stop"], 100)
        q = d["qty"] + 1
        one_more = q * (p["entry"] - p["stop"]) + m.trade_costs(q, p["entry"], p["stop"], "intraday")["total"]
        self.assertGreater(one_more, 100)
        self.assertIn("max loss", d["decided_by"])
        self.assertLess(d["net_rr"], p["r_multiple"])

    def test_position_cap(self):
        self.chain()
        self.run_skill("budget", now="2026-10-21 10:06")
        d = self.read("sizing.json")
        self.assertIn("position cap", d["decided_by"])
        self.assertLessEqual(d["value"], 25000)

    def test_too_small_budget(self):
        self.chain()
        rc, out = self.run_skill("budget", "--max-loss", "3", now="2026-10-21 10:06")
        self.assertEqual(self.read("sizing.json")["suggestion"], "skip")
        self.assertIn("even 1 share", out)

    def test_checks_use_odds_news_and_window(self):
        self.chain()
        self.run_skill("tm", "run", "--replays", "2000", now="2026-10-21 10:06")
        self.run_skill("news", "--tickers", "DEMOBRK", now="2026-10-21 10:06")
        rc, out = self.run_skill("budget", now="2026-10-21 10:06")
        texts = [c["text"] for c in self.read("sizing.json")["checks"]]
        self.assertTrue(any("time machine evidence" in t for t in texts))
        self.assertTrue(any("news flag" in t and "Auditor" in t for t in texts))
        rc, out = self.run_skill("budget", now="2026-10-21 14:40")
        self.assertIn("past your no-entries time", out)

    def test_daily_limits_from_journal(self):
        self.chain()
        self.write("journal.json", {"next": 4, "entries": [
            {"id": "J%d" % i, "taken": True, "taken_at": "2026-10-21T09:5%d:00+05:30" % i, "status": "closed",
             "pnl": -200} for i in (1, 2, 3)]})
        rc, out = self.run_skill("budget", now="2026-10-21 10:06")
        self.assertIn("you have taken 3 trades today", out)


if __name__ == "__main__":
    unittest.main()
