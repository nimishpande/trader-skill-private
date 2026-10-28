"""Build tests/fixtures/nse-demo.json.gz: SYNTHETIC NSE data for the offline tests.

Every symbol here is made up (DEMO...). The numbers are generated, not real
market data. They are shaped like Yahoo Finance's answers for NSE stocks
(SYMBOL.NS, India time, 5-minute bars from 9:15) and built so each rule has a
case that fires and a case that does not:

  DEMOBRK   range-bound for 60 sessions, then on Wed 21 Oct 2026 its 10:00 bar
            closes above the 9:15-9:30 opening range (trigger at 10:05) and the
            day closes at a 20-day closing high
  DEMOFLAT  stays inside its opening range all day; no daily setup
  DEMOFLAG  +15% in two weeks, then five tight days: a bull flag
  DEMODIP   slides to RSI under 30, then closes up: an oversold bounce
  DEMOGAP   breaks out but its 9:50 bar is missing (a halt): unusable
  DEMOPENNY a 12-rupee trade-to-trade (BE series) stock: under the price floor

Run:  python3 tests/make_fixtures.py   (deterministic; same file every time)
"""
import datetime
import gzip
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import indmkt as m  # noqa: E402

OUT = os.path.join(HERE, "fixtures", "nse-demo.json.gz")
TEST_DAY = datetime.date(2026, 10, 21)
CAL = m.Calendar()
NAMES = {
    "DEMOBRK": ("Demo Breakout Ltd", "Capital Goods", "INE0DEMO1018", "EQ", 599001),
    "DEMOFLAT": ("Demo Flatline Ltd", "FMCG", "INE0DEMO2016", "EQ", 599002),
    "DEMOFLAG": ("Demo Flag Industries Ltd", "Automobile", "INE0DEMO3014", "EQ", 599003),
    "DEMODIP": ("Demo Dip Pharma Ltd", "Healthcare", "INE0DEMO4012", "EQ", 599004),
    "DEMOGAP": ("Demo Gap Metals Ltd", "Metals & Mining", "INE0DEMO5019", "EQ", 599005),
    "DEMOPENNY": ("Demo Penny Infra Ltd", "Construction", "INE0DEMO6017", "BE", 599006),
}


def sessions(start, end):
    d, out = start, []
    while d <= end:
        if CAL.session(d):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


DAYS = sessions(datetime.date(2024, 9, 2), TEST_DAY)          # ~540 sessions of daily bars
INTRA_DAYS = DAYS[-61:]                                       # 60 past sessions + the test day


def ts_of(d, hh, mm, ss=0):
    return int(datetime.datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=m.IST).timestamp())


def r2(x):
    return round(x, 2)


def chart_doc(sym, bars, interval):
    """bars: list of (ts, o, h, l, c, v) -> Yahoo v8 chart JSON."""
    last = bars[-1]
    prev_close = bars[-2][4] if len(bars) > 1 else last[1]
    meta = {"currency": "INR", "symbol": sym + ".NS", "exchangeName": "NSI", "fullExchangeName": "NSE",
            "instrumentType": "EQUITY", "gmtoffset": 19800, "timezone": "IST",
            "exchangeTimezoneName": "Asia/Kolkata", "regularMarketPrice": last[4],
            "regularMarketTime": last[0], "regularMarketVolume": last[5],
            "chartPreviousClose": prev_close, "previousClose": prev_close, "dataGranularity": interval}
    return {"chart": {"result": [{"meta": meta, "timestamp": [b[0] for b in bars],
                                  "indicators": {"quote": [{
                                      "open": [b[1] for b in bars], "high": [b[2] for b in bars],
                                      "low": [b[3] for b in bars], "close": [b[4] for b in bars],
                                      "volume": [b[5] for b in bars]}]}}], "error": None}}


def walk_day(rng, d, p0, level, sigma=0.0012, kappa=0.004, vol=20000):
    """One session of 75 five-minute bars, pulled gently toward `level`."""
    s = CAL.session(d)
    n = int((s[1] - s[0]).total_seconds() // 300)
    out, p = [], p0
    for k in range(n):
        t = int(s[0].timestamp()) + 300 * k
        o = p
        ret = rng.gauss(0, sigma) + kappa * (level - p) / p
        c = o * (1 + ret)
        h = max(o, c) * (1 + abs(rng.gauss(0, sigma / 2)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, sigma / 2)))
        out.append([t, r2(o), r2(h), r2(lo), r2(c), int(vol * (0.5 + rng.random()))])
        p = c
    return out


def daily_from(intra):
    return [intra[0][0] - 0, intra[0][1], max(b[2] for b in intra), min(b[3] for b in intra),
            intra[-1][4], sum(b[5] for b in intra)]


def daily_walk(rng, days, p0, drift=0.0, sigma=0.012, vol=900000):
    out, p = [], p0
    for d in days:
        o = p * (1 + rng.gauss(0, sigma / 4))
        c = o * (1 + drift + rng.gauss(0, sigma))
        h = max(o, c) * (1 + abs(rng.gauss(0, sigma / 2)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, sigma / 2)))
        out.append([ts_of(d, 9, 15), r2(o), r2(h), r2(lo), r2(c), int(vol * (0.6 + rng.random()))])
        p = c
    return out


def set_day(bar, o, h, lo, c, v=None):
    bar[1], bar[2], bar[3], bar[4] = r2(o), r2(h), r2(lo), r2(c)
    if v is not None:
        bar[5] = v


def breakout_day(rng, d, prev_close, max20, vol=20000):
    """The DEMOBRK test day: opening range 9:15-9:30, 10:00 bar closes above it."""
    P = prev_close
    orh = r2(P * 1.004)
    s = CAL.session(d)
    bars = []
    t0 = int(s[0].timestamp())
    final = max(P * 1.012, max20 * 1.012)
    for k in range(75):
        t = t0 + 300 * k
        if k < 3:                                   # 9:15, 9:20, 9:25: the range
            o = P * (1 + rng.uniform(-0.002, 0.002))
            c = P * (1 + rng.uniform(-0.002, 0.002))
            h = orh if k == 1 else max(o, c) * 1.0005
            lo = min(o, c) * 0.997
        elif k < 9:                                 # 9:30 to 9:55: below the range high
            o = P * (1 + rng.uniform(-0.001, 0.003))
            c = P * (1 + rng.uniform(-0.001, 0.003))
            h = min(orh - 0.05, max(o, c) * 1.0005)
            lo = min(o, c) * 0.999
        elif k == 9:                                # 10:00 bar: closes above, trigger at 10:05
            o = P * 1.002
            c = P * 1.006
            h = c * 1.0008
            lo = o * 0.9995
        else:                                       # drift to the close
            frac = (k - 9) / 65.0
            base = P * 1.006 + (final - P * 1.006) * frac
            o = bars[-1][4]
            c = base * (1 + rng.uniform(-0.0006, 0.0006)) if k < 74 else final
            h = max(o, c) * (1 + rng.uniform(0, 0.0008))
            lo = min(o, c) * (1 - rng.uniform(0, 0.0008))
        bars.append([t, r2(o), r2(h), r2(lo), r2(c), int(vol * (0.6 + rng.random()) * (2.5 if k == 9 else 1))])
    return bars


def flat_day(rng, d, P, vol=15000):
    s = CAL.session(d)
    t0 = int(s[0].timestamp())
    bars = []
    for k in range(75):
        o = P * (1 + rng.uniform(-0.0015, 0.0015))
        c = P * (1 + rng.uniform(-0.0015, 0.0015))
        h = P * 1.004 if k == 1 else max(o, c) * 1.0004  # the 9:20 bar sets the range high
        lo = min(o, c) * 0.9996
        bars.append([t0 + 300 * k, r2(o), r2(h), r2(lo), r2(c), int(vol * (0.6 + rng.random()))])
    return bars


def build():
    rng = random.Random(2610)
    bundle = {}

    # DEMOBRK: daily history, then 60 range-bound intraday sessions, then the breakout day
    hist = daily_walk(rng, DAYS[:-61], 760.0, drift=0.0006)
    p = hist[-1][4]
    level = 1000.0
    intra = []
    # glide from the daily walk to the 1000 level over the intraday sessions
    for i, d in enumerate(INTRA_DAYS[:-1]):
        lv = p + (level - p) * min(1.0, (i + 1) / 10.0)
        day = walk_day(rng, d, p, lv)
        intra.append(day)
        p = day[-1][4]
    closes20 = [day[-1][4] for day in intra[-20:]]
    test = breakout_day(rng, TEST_DAY, p, max(closes20))
    intra.append(test)
    flat5m = [b for day in intra for b in day]
    # Yahoo quirks: one bar with a missing price, and the live tick off the 5-minute grid
    flat5m.insert(-30, [ts_of(TEST_DAY, 13, 0) + 1, None, None, None, None, None])
    flat5m.append([ts_of(TEST_DAY, 15, 29, 41), test[-1][4], test[-1][4], test[-1][4], test[-1][4], 1200])
    flat5m.sort(key=lambda b: b[0])
    bundle["chart:DEMOBRK:5m"] = chart_doc("DEMOBRK", flat5m, "5m")
    daily = hist + [daily_from(day) for day in intra]
    for b, d in zip(daily[-61:], INTRA_DAYS):
        b[0] = ts_of(d, 9, 15)
    bundle["chart:DEMOBRK:1d"] = chart_doc("DEMOBRK", daily, "1d")

    # DEMOFLAT: sideways, inside its range on the test day
    hist = daily_walk(rng, DAYS[:-61], 480.0, drift=0.0)
    p = hist[-1][4]
    intra = []
    for d in INTRA_DAYS[:-1]:
        day = walk_day(rng, d, p, 480.0, sigma=0.0009)
        intra.append(day)
        p = day[-1][4]
    intra.append(flat_day(rng, TEST_DAY, p))
    bundle["chart:DEMOFLAT:5m"] = chart_doc("DEMOFLAT", [b for day in intra for b in day], "5m")
    daily = hist + [daily_from(day) for day in intra]
    for b, d in zip(daily[-61:], INTRA_DAYS):
        b[0] = ts_of(d, 9, 15)
    # make sure the last close is not a 20-day high, a flag or an MA reclaim
    last = daily[-1]
    set_day(last, last[1], last[2], last[3], min(last[4], min(b[4] for b in daily[-21:-1]) * 1.001))
    bundle["chart:DEMOFLAT:1d"] = chart_doc("DEMOFLAT", daily, "1d")

    # DEMOFLAG: steady, a 15% run, then five tight closes
    hist = daily_walk(rng, DAYS[:-15], 300.0, drift=0.0003, sigma=0.008)
    p = hist[-1][4]
    run = []
    for d in DAYS[-15:-5]:
        o = p
        c = p * 1.015
        run.append([ts_of(d, 9, 15), r2(o), r2(c * 1.004), r2(o * 0.997), r2(c), 1500000])
        p = c
    top = p
    for k, d in enumerate(DAYS[-5:]):
        c = top * (1 + [0.004, -0.006, 0.002, -0.004, 0.006][k])
        run.append([ts_of(d, 9, 15), r2(p), r2(max(p, c) * 1.003), r2(min(p, c) * 0.997), r2(c), 1300000])
        p = c
    bundle["chart:DEMOFLAG:1d"] = chart_doc("DEMOFLAG", hist + run, "1d")

    # DEMODIP: a slide to RSI under 30, then an up close on the test day
    hist = daily_walk(rng, DAYS[:-13], 1500.0, drift=0.0002)
    p = hist[-1][4]
    slide = []
    for d in DAYS[-13:-1]:
        c = p * 0.975
        slide.append([ts_of(d, 9, 15), r2(p), r2(p * 1.002), r2(c * 0.996), r2(c), 2200000])
        p = c
    slide.append([ts_of(DAYS[-1], 9, 15), r2(p), r2(p * 1.03), r2(p * 0.995), r2(p * 1.025), 3100000])
    bundle["chart:DEMODIP:1d"] = chart_doc("DEMODIP", hist + slide, "1d")

    # DEMOGAP: breaks out, but the 9:50 bar is missing
    hist = daily_walk(rng, DAYS[:-1], 220.0)
    p = hist[-1][4]
    day = breakout_day(rng, TEST_DAY, p, p)
    day = [b for b in day if m.from_ts(b[0]).strftime("%H:%M") != "09:50"]
    bundle["chart:DEMOGAP:5m"] = chart_doc("DEMOGAP", day, "5m")
    bundle["chart:DEMOGAP:1d"] = chart_doc("DEMOGAP", hist + [daily_from(day)], "1d")

    # DEMOPENNY: twelve rupees, trade-to-trade
    hist = daily_walk(rng, DAYS[:-1], 12.0, vol=5000000)
    p = hist[-1][4]
    day = breakout_day(rng, TEST_DAY, p, p, vol=400000)
    bundle["chart:DEMOPENNY:5m"] = chart_doc("DEMOPENNY", day, "5m")
    bundle["chart:DEMOPENNY:1d"] = chart_doc("DEMOPENNY", hist + [daily_from(day)], "1d")

    # NSE index list (ind_nifty500list.csv layout) and the all-equities list (EQUITY_L.csv layout)
    rows = ["Company Name,Industry,Symbol,Series,ISIN Code"]
    for s, (name, ind, isin, series, _) in NAMES.items():
        rows.append("%s,%s,%s,%s,%s" % (name, ind, s, series, isin))
    bundle["nse:nifty500"] = "\n".join(rows) + "\n"
    rows = ["SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE"]
    for s, (name, ind, isin, series, _) in NAMES.items():
        rows.append("%s,%s,%s,01-JAN-2010,10,1,%s,10" % (s, name, series, isin))
    rows.append("DEMOSME,Demo SME Ltd,SM,01-JAN-2020,10,1,INE0DEMO7015,10")
    bundle["nse:all"] = "\n".join(rows) + "\n"

    # Headlines (Yahoo RSS) and BSE announcements for DEMOBRK
    def rfc(dt):
        return dt.astimezone(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = [
        (datetime.datetime(2026, 10, 21, 8, 45, tzinfo=m.IST), "Demo Breakout Q2 results: net profit rises 18% YoY, beats estimates", "https://example.com/n1"),
        (datetime.datetime(2026, 10, 21, 11, 2, tzinfo=m.IST), "Brokerage upgrades Demo Breakout to buy, raises target price", "https://example.com/n2"),
        (datetime.datetime(2026, 10, 14, 9, 0, tzinfo=m.IST), "Demo Breakout wins Rs 450 crore order from railways", "https://example.com/n3"),
    ]
    xml = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Yahoo! Finance: DEMOBRK.NS News</title>']
    for dt, title, link in items:
        xml.append("<item><title>%s</title><link>%s</link><pubDate>%s</pubDate><guid>%s</guid></item>" % (title, link, rfc(dt), link))
    xml.append("</channel></rss>")
    bundle["rss:DEMOBRK"] = "".join(xml)
    bundle["rss:DEMOFLAT"] = '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
    bundle["bse_lookup:DEMOBRK"] = ("<li class='quotemenu'><a><strong>DEMOBRK</strong>   INE0DEMO1018   599001"
                                    "<br/><span>DEMO BREAKOUT LTD</span></a></li>")
    bundle["bse_lookup:DEMOFLAT"] = ("<li><a><strong>DEMOFLAT</strong>   INE0DEMO2016   599002</a></li>")
    bundle["bse_ann:599001"] = {"Table": [
        {"NEWSID": "d1", "SCRIP_CD": 599001, "NEWSSUB": "DEMO BREAKOUT LTD - 599001 - Outcome of Board Meeting",
         "NEWS_DT": "2026-10-21T08:30:12.5", "DissemDT": "2026-10-21T08:30:12.5",
         "HEADLINE": "Outcome of Board Meeting - Financial Results for the quarter ended September 30, 2026",
         "CATEGORYNAME": "Result", "SUBCATNAME": "Financial Results", "ATTACHMENTNAME": "d1.pdf",
         "SLONGNAME": "DEMO BREAKOUT LTD"},
        {"NEWSID": "d2", "SCRIP_CD": 599001, "NEWSSUB": "DEMO BREAKOUT LTD - 599001 - Resignation of Statutory Auditors",
         "NEWS_DT": "2026-10-20T18:05:00", "DissemDT": "2026-10-20T18:05:00",
         "HEADLINE": "Intimation of resignation of the Statutory Auditor of the Company",
         "CATEGORYNAME": "Company Update", "SUBCATNAME": "Resignation of Statutory Auditors",
         "ATTACHMENTNAME": "d2.pdf", "SLONGNAME": "DEMO BREAKOUT LTD"},
    ], "Table1": [{"ROWCNT": 2}]}
    bundle["bse_ann:599002"] = {"Table": [], "Table1": [{"ROWCNT": 0}]}
    bundle["_about"] = "SYNTHETIC test data for the India Trader skills. Not real market data. See tests/make_fixtures.py."
    return bundle


def main():
    bundle = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    raw = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with open(OUT, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
            gz.write(raw)
    sys.stdout.write("Wrote %s (%d KB, %d keys)\n" % (OUT, os.path.getsize(OUT) // 1024, len(bundle)))


if __name__ == "__main__":
    main()
