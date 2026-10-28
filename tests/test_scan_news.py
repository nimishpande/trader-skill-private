import csv
import os
import unittest

from _util import SkillCase

import indmkt as m


class TestUniverse(SkillCase):
    def test_index_list(self):
        rc, out = self.run_skill("scan", "universe")
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.tdir, "universe.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 6)
        self.assertEqual({r["series"] for r in rows}, {"EQ", "BE"})
        self.assertIn("1 trade-to-trade", out)

    def test_all_equities_drops_sme(self):
        rc, out = self.run_skill("scan", "universe", "--index", "all")
        self.assertEqual(rc, 0, out)
        self.assertIn("SM 1", out)

    def test_bad_csv(self):
        bad = os.path.join(self.tmp, "bad.csv")
        with open(bad, "w") as fh:
            fh.write("foo,bar\n1,2\n")
        rc, out = self.run_skill("scan", "universe", "--file", bad, offline=False)
        self.assertEqual(rc, 1)
        self.assertIn("no Symbol column", out)

    def test_drops_placeholder_rows(self):
        saved = os.path.join(self.tmp, "nifty500.csv")
        with open(saved, "w") as fh:
            fh.write("Company Name,Industry,Symbol,Series,ISIN Code\n"
                     "Demo Breakout Ltd,Capital Goods,DEMOBRK,EQ,INE0DEMO1011\n"
                     "Dummy Demo Ltd,Capital Goods,DUMMYDEMO,EQ,DUM0DEMO1011\n"
                     "Demo REIT,Realty,DEMOREIT,RR,INE0DEMO2011\n")
        rc, out = self.run_skill("scan", "universe", "--file", saved, offline=False)
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.tdir, "universe.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([r["symbol"] for r in rows], ["DEMOBRK"])
        self.assertIn("placeholder rows (not real stocks): DUMMYDEMO", out)
        self.assertIn("RR 1", out)


class TestSwingScan(SkillCase):
    def test_hits(self):
        self.run_skill("scan", "universe")
        rc, out = self.run_skill("scan", "swing", now="2026-10-22 08:30")
        self.assertEqual(rc, 0, out)
        wl = self.read("watchlist.json")
        hits = {h["symbol"]: h for h in wl["hits"]}
        self.assertIn("breakout_20d", hits["DEMOBRK"]["setups"])
        self.assertIn("bull_flag", hits["DEMOFLAG"]["setups"])
        self.assertIn("oversold_bounce", hits["DEMODIP"]["setups"])
        self.assertNotIn("DEMOFLAT", hits)
        self.assertNotIn("DEMOPENNY", hits)                          # Rs 12: under the Rs 20 floor
        b = hits["DEMOBRK"]
        self.assertGreater(b["trigger"], b["high"])                  # one tick above the signal day's high
        self.assertAlmostEqual(b["trigger"], m.ceil_tick(b["high"] + m.tick_value(b["high"])), places=2)
        self.assertAlmostEqual(b["trigger"], m.ceil_tick(b["trigger"]), places=2)           # on the NSE grid
        self.assertEqual(wl["data_through"], "2026-10-21")

    def test_session_open_reads_yesterday(self):
        rc, out = self.run_skill("scan", "swing", "--symbols", "DEMOBRK", now="2026-10-21 11:00")
        self.assertIn("reads yesterday's close", out)


class TestORBScan(SkillCase):
    def setUp(self):
        super().setUp()
        self.run_skill("scan", "universe")

    def test_breakout_at_1005(self):
        rc, out = self.run_skill("scan", "orb", now="2026-10-21 10:15")
        self.assertEqual(rc, 0, out)
        wl = self.read("watchlist.json")
        self.assertEqual([h["symbol"] for h in wl["hits"]], ["DEMOBRK"])
        h = wl["hits"][0]
        self.assertEqual(h["hit_time"], "10:05")
        self.assertGreater(h["hit_close"], h["or_high"])
        self.assertEqual(wl["usable"], 2)                            # DEMOGAP has a missing bar
        self.assertEqual(wl["excluded_t2t"], ["DEMOPENNY"])

    def test_nothing_before_the_trigger(self):
        rc, out = self.run_skill("scan", "orb", now="2026-10-21 10:02")
        self.assertEqual(rc, 1)
        self.assertIn("No usable chart", out)

    def test_too_early_holiday_and_late(self):
        rc, out = self.run_skill("scan", "orb", now="2026-10-21 09:32")
        self.assertIn("Too early", out)
        rc, out = self.run_skill("scan", "orb", now="2026-10-20 11:00")
        self.assertIn("Dussehra", out)
        rc, out = self.run_skill("scan", "orb", now="2026-10-21 14:45")
        self.assertIn("no_entries_after", out)
        rc, out = self.run_skill("scan", "orb", "--at", "10:15", now="2026-10-21 14:45")
        self.assertEqual(rc, 0, out)

    def test_needs_a_list(self):
        os.remove(os.path.join(self.tdir, "universe.csv"))
        rc, out = self.run_skill("scan", "orb", now="2026-10-21 10:15")
        self.assertIn("scan.py universe", out)


class TestNews(SkillCase):
    def setUp(self):
        super().setUp()
        self.run_skill("scan", "universe")

    def test_items_tags_and_flags(self):
        rc, out = self.run_skill("news", "--tickers", "DEMOBRK", now="2026-10-21 11:30")
        self.assertEqual(rc, 0, out)
        doc = self.read("news.json")
        items = doc["stocks"][0]["items"]
        self.assertEqual([i["source"] for i in items], ["Yahoo", "Yahoo", "BSE", "BSE"])   # newest first
        auditor = [i for i in items if "auditor" in i["tags"]]
        self.assertEqual(len(auditor), 1)
        self.assertTrue(auditor[0]["flag"])
        self.assertTrue(auditor[0]["url"].endswith("/AttachLive/d2.pdf"))
        self.assertIn("analyst", items[0]["tags"])
        self.assertIn("results", items[1]["tags"])
        self.assertEqual(self.read("bse_codes.json")["DEMOBRK"], "599001")
        mv = doc["stocks"][0]["move"]
        self.assertEqual(mv["label"], "vs yesterday's close")

    def test_only_new_items_second_time(self):
        self.run_skill("news", "--tickers", "DEMOBRK", now="2026-10-21 11:30")
        rc, out = self.run_skill("news", "--tickers", "DEMOBRK", now="2026-10-21 11:40")
        self.assertIn("nothing new", out)
        rc, out = self.run_skill("news", "--tickers", "DEMOBRK", "--all", now="2026-10-21 11:40")
        self.assertIn("Brokerage upgrades", out)

    def test_window(self):
        rc, out = self.run_skill("news", "--tickers", "DEMOBRK", "--hours", "200", "--all", now="2026-10-21 16:00")
        self.assertIn("Rs 450 crore order", out)
        rc, out = self.run_skill("news", "--tickers", "DEMOBRK", "--all", now="2026-10-21 16:00")
        self.assertNotIn("Rs 450 crore order", out)

    def test_tagger(self):
        import importlib.util
        from _util import SKILLS
        spec = importlib.util.spec_from_file_location("news", os.path.join(SKILLS, "trader-news", "news.py"))
        news = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(news)
        self.assertEqual(news.tag("Board meeting to consider and approve financial results for Q2")[0][:1], ["results_due"])
        self.assertTrue(news.tag("Creation of pledge on promoter shares")[1])
        self.assertTrue(news.tag("SEBI order imposing penalty on the company")[1])
        self.assertIn("deal", news.tag("Company bags Rs 300 crore order from NHAI")[0])
        self.assertFalse(news.tag("Company bags Rs 300 crore order from NHAI")[1])


if __name__ == "__main__":
    unittest.main()
