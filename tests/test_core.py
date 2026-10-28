import datetime
import filecmp
import os
import unittest

from _util import ROOT, SKILLS, BUNDLE
import indmkt as m


class TestCalendar(unittest.TestCase):
    def setUp(self):
        self.cal = m.Calendar()

    def test_regular_day(self):
        s = self.cal.session(datetime.date(2026, 10, 21))
        self.assertEqual((m.clock(s[0]), m.clock(s[1])), ("09:15", "15:30"))

    def test_holidays_and_weekends(self):
        self.assertIsNone(self.cal.session(datetime.date(2026, 10, 20)))     # Dussehra
        self.assertIsNone(self.cal.session(datetime.date(2026, 1, 15)))      # Maharashtra municipal elections
        self.assertIsNone(self.cal.session(datetime.date(2026, 10, 24)))     # Saturday
        self.assertIn("Dussehra", self.cal.closed_reason(datetime.date(2026, 10, 20)))

    def test_muhurat_session_on_a_sunday(self):
        s = self.cal.session(datetime.date(2026, 11, 8))
        self.assertEqual((m.clock(s[0]), m.clock(s[1])), ("14:20", "15:30"))
        self.assertIn("Muhurat", s[2])

    def test_status(self):
        self.assertEqual(self.cal.status(m.now_ist("2026-10-21 10:00"))[0], "open")
        self.assertEqual(self.cal.status(m.now_ist("2026-10-21 09:05"))[0], "pre-open")
        st, msg = self.cal.status(m.now_ist("2026-10-23 16:00"))
        self.assertEqual(st, "closed")
        self.assertIn("Mon 26 Oct 2026", msg)

    def test_unknown_year_warns(self):
        self.assertIn("extra_holidays", self.cal.status(m.now_ist("2026-12-31 16:00"))[1])

    def test_config_holidays(self):
        cal = m.Calendar({"extra_holidays": {"2027-01-26": "Republic Day"}})
        self.assertIsNone(cal.session(datetime.date(2027, 1, 26)))
        self.assertTrue(cal.known(datetime.date(2027, 3, 1)))


class TestTicksAndMoney(unittest.TestCase):
    def test_tick_bands(self):
        cases = [(99.99, 0.01), (250.0, 0.01), (251.3, 0.05), (999.0, 0.05), (1000.1, 0.10),
                 (7000, 0.50), (15000, 1.0), (30000, 5.0)]
        for px, tick in cases:
            self.assertAlmostEqual(m.tick_value(px), tick, msg=px)

    def test_rounding(self):
        self.assertAlmostEqual(m.floor_tick(999.99), 999.95)
        self.assertAlmostEqual(m.ceil_tick(999.96), 1000.0)
        self.assertAlmostEqual(m.floor_tick(1234.56), 1234.5)
        self.assertAlmostEqual(m.floor_tick(123.456), 123.45)
        self.assertAlmostEqual(m.floor_tick(1000.30), 1000.30)   # already on the grid: no float drift

    def test_override(self):
        self.assertAlmostEqual(m.tick_value(1500, "ABC", {"tick_overrides": {"ABC": 0.05}}), 0.05)

    def test_indian_grouping(self):
        self.assertEqual(m.inr(1234567.5), "₹12,34,567.50")
        self.assertEqual(m.inr(100000, 0), "₹1,00,000")
        self.assertEqual(m.inr(-999), "-₹999.00")


class TestCosts(unittest.TestCase):
    def test_intraday_by_hand(self):
        c = m.trade_costs(10, 2914.5, 2951.0, "intraday")
        # brokerage min(20, 0.03%) each side; STT 0.025% of the sell; exchange 0.00307% of turnover;
        # SEBI Rs 10/crore; GST 18% of (brokerage + exchange + SEBI); stamp 0.003% of the buy
        self.assertEqual(c["brokerage"], 17.6)
        self.assertEqual(c["stt"], 7.38)
        self.assertEqual(c["exchange"], 1.8)
        self.assertEqual(c["sebi"], 0.06)
        self.assertEqual(c["gst"], 3.5)
        self.assertEqual(c["stamp"], 0.87)
        self.assertEqual(c["total"], 31.21)

    def test_brokerage_cap(self):
        c = m.trade_costs(1000, 2000, 2000, "intraday")
        self.assertEqual(c["brokerage"], 40.0)                     # Rs 20 per order, two orders

    def test_delivery(self):
        c = m.trade_costs(10, 2914.5, 2951.0, "delivery")
        self.assertEqual(c["brokerage"], 0.0)
        self.assertEqual(c["stt"], round((29145 + 29510) * 0.001, 2))
        self.assertEqual(c["dp"], 15.34)
        self.assertGreater(c["total"], m.trade_costs(10, 2914.5, 2951.0, "intraday")["total"])

    def test_broker_override(self):
        c = m.trade_costs(10, 1000, 1000, "delivery", {"brokerage_delivery_pct": 0.1, "brokerage_delivery_cap": 20})
        self.assertEqual(c["brokerage"], 20.0)


class TestBarsAndIndicators(unittest.TestCase):
    def setUp(self):
        self.src = m.Source(offline=BUNDLE)
        self.cal = m.Calendar()

    def test_intraday_quirks_dropped(self):
        bars, dropped = m.intraday_bars(self.src.chart("DEMOBRK", "60d", "5m"), self.cal, m.now_ist("2026-10-21 16:00"))
        self.assertEqual(dropped["no_price"], 1)
        self.assertEqual(dropped["off_grid"], 1)                  # Yahoo's live tick at 15:29:41
        self.assertEqual(len([b for b in bars if b["date"] == "2026-10-21"]), 75)
        self.assertEqual(bars[-1]["time"], "15:25")

    def test_spark_is_close_only_like_yahoo(self):
        got, failed = self.src.spark(["DEMOBRK", "NOSUCH"], "1d", "5m")
        self.assertEqual(failed, ["NOSUCH"])
        ser = got["DEMOBRK"]
        self.assertTrue(ser["close_only"])
        self.assertIsNone(ser["o"][0])
        bars, _ = m.intraday_bars(ser, self.cal, m.now_ist("2026-10-21 10:15"))
        today = [b for b in bars if b["date"] == "2026-10-21"]
        self.assertEqual(len(today), 12)
        self.assertEqual(today[0]["h"], today[0]["c"])
        full, _ = m.intraday_bars({"t": ser["t"], "c": ser["c"], "o": ser["o"], "h": ser["h"], "l": ser["l"],
                                   "v": ser["v"], "meta": {}}, self.cal, m.now_ist("2026-10-21 10:15"))
        self.assertEqual(full, [])                                # a chart missing OHLC is not silently accepted

    def test_forming_bar_left_out(self):
        bars, d = m.intraday_bars(self.src.chart("DEMOBRK", "1d", "5m"), self.cal, m.now_ist("2026-10-21 10:08"))
        today = [b for b in bars if b["date"] == "2026-10-21"]
        self.assertEqual(today[-1]["time"], "10:00")              # 10:05 bar is still forming at 10:08

    def test_daily_excludes_today_until_close(self):
        ser = self.src.chart("DEMOBRK", "2y", "1d")
        self.assertEqual(m.daily_bars(ser, self.cal, m.now_ist("2026-10-21 11:00"))[-1]["date"], "2026-10-19")
        self.assertEqual(m.daily_bars(ser, self.cal, m.now_ist("2026-10-21 15:31"))[-1]["date"], "2026-10-21")

    def test_rsi_bounds_and_setups(self):
        cases = {"DEMOBRK": "breakout_20d", "DEMOFLAG": "bull_flag", "DEMODIP": "oversold_bounce"}
        now = m.now_ist("2026-10-22 08:30")
        for sym, setup in cases.items():
            c = [b["c"] for b in m.daily_bars(self.src.chart(sym, "2y", "1d"), self.cal, now)]
            r = m.rsi_series(c)
            self.assertTrue(all(x is None or 0 <= x <= 100 for x in r))
            self.assertIn(setup, m.setups_at(c, len(c) - 1, r), sym)
        c = [b["c"] for b in m.daily_bars(self.src.chart("DEMOFLAT", "2y", "1d"), self.cal, now)]
        self.assertEqual(m.setups_at(c, len(c) - 1, m.rsi_series(c)), [])

    def test_atr_positive(self):
        bars = m.daily_bars(self.src.chart("DEMOBRK", "2y", "1d"), self.cal, m.now_ist("2026-10-22 08:30"))
        atr = m.atr_series(bars)
        self.assertIsNone(atr[12])
        self.assertGreater(atr[-1], 0)


class TestCopies(unittest.TestCase):
    def test_every_skill_carries_the_same_core(self):
        src = os.path.join(ROOT, "core", "indmkt.py")
        dirs = [d for d in os.listdir(SKILLS) if d.startswith("trader-")]
        self.assertEqual(len(dirs), 9)
        for d in dirs:
            self.assertTrue(filecmp.cmp(src, os.path.join(SKILLS, d, "indmkt.py"), shallow=False),
                            "%s/indmkt.py drifted: run tools/sync_core.py" % d)


if __name__ == "__main__":
    unittest.main()
