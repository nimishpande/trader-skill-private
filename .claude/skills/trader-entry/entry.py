"""trader-entry: has the setup triggered yet? Suggestions only; it never orders.

  entry.py check SYMBOL [--setup orb|level|swing] [--level PRICE] [--min-volume-ratio 1.5]
  entry.py watch SYMBOL [--every 60] [same options]   (re-checks until triggered or expired)

orb    intraday: the first completed 5-minute bar that CLOSES above the high of
       the opening range (9:15 to 9:30 by default). Long side only.
level  intraday: the first completed 5-minute bar that closes above --level.
swing  the first trade at the swing trigger (one tick above the signal day's
       high, from the swing scan, or --level) within --max-days sessions: what a
       buy-stop order there would do. A gap open above it fills at the open.

States: not open / range forming / waiting (how far) / triggered (when, at what
price, how far it has run since) / expired. Writes trader/entry.json.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indmkt as m  # noqa: E402

STALE_AFTER_PCT = 0.5   # a trigger the price has run this far past is flagged stale


def _today(bars, d):
    return [b for b in bars if b["date"] == d.isoformat()]


def _end(b):
    return m.from_ts(b["ts"]) + datetime.timedelta(minutes=5)


def swing_context(src, sym, cal, now):
    """ATR and recent lows from completed daily bars (today excluded until the close)."""
    bars = m.daily_bars(src.chart(sym, "1y", "1d"), cal, now)
    if len(bars) < 15:
        raise m.MarketError("not enough daily history for %s" % sym)
    atr = m.atr_series(bars)
    return {"atr14": round(atr[-1], 2) if atr[-1] else None,
            "low_10d": round(min(b["l"] for b in bars[-10:]), 2),
            "prev_close": round(bars[-1]["c"], 2), "daily": bars}


def find_trigger(bars, level, start=0, min_vol=None):
    for b in bars[start:]:
        if b["c"] > level and (min_vol is None or b["v"] >= min_vol):
            return b
    return None


def check(a, cfg, cal, src, now):
    sym = m.clean_symbol(a.symbol)
    setup = a.setup
    doc = {"symbol": sym, "setup": setup, "as_of": now.isoformat(), "mode": "swing" if setup == "swing" else "intraday"}
    s = cal.session(now.date())
    intra = cfg["intraday"]
    late = datetime.datetime.combine(now.date(), m.parse_hhmm(intra["no_entries_after"]), m.IST)

    if setup == "swing":
        wl = m.read_json(m.tpath("watchlist.json", a.trader_dir), {}) or {}
        hit = next((h for h in wl.get("hits", []) if h.get("symbol") == sym and wl.get("mode") == "swing"), None)
        level = a.level or (hit or {}).get("trigger")
        signal = (hit or {}).get("date")
        if not level:
            raise m.MarketError("No swing trigger for %s. Run the swing scan first or pass --level PRICE." % sym)
        ctx = swing_context(src, sym, cal, now)
        doc.update({"level": level, "signal_date": signal, "atr14": ctx["atr14"], "low_10d": ctx["low_10d"]})
        after = [b for b in ctx["daily"] if signal and b["date"] > signal]
        for b in after:                                  # an earlier session already traded through it?
            if b["h"] >= level:
                doc.update({"state": "triggered_earlier", "trigger_date": b["date"],
                            "entry": round(max(b["o"], level), 2), "last": ctx["prev_close"]})
                return doc
        if signal and len(after) >= a.max_days:
            doc.update({"state": "expired", "why": "%d sessions since the signal without a close above %s; rescan"
                        % (len(after), m.price(level)), "last": ctx["prev_close"]})
            return doc
        if not s or now < s[0]:
            doc.update({"state": "not_open", "last": ctx["prev_close"],
                        "distance_pct": round((level / ctx["prev_close"] - 1) * 100, 2)})
            return doc
        try:
            bars = _today(m.intraday_bars(src.chart(sym, "1d", "5m"), cal, now)[0], now.date())
        except m.MarketError as e:
            bars = []
            doc["why"] = "today's 5-minute bars are not available (%s); showing yesterday's close" % e
        last = bars[-1]["c"] if bars else ctx["prev_close"]
        trig = next((b for b in bars if b["h"] >= level), None)     # a buy-stop at the level fills here
        if trig:
            doc.update({"state": "triggered", "trigger_time": m.clock(_end(trig)), "trigger_date": now.date().isoformat(),
                        "entry": round(max(trig["o"], level), 2), "last": round(last, 2)})
        else:
            doc.update({"state": "waiting", "last": round(last, 2), "distance_pct": round((level / last - 1) * 100, 2)})
        return doc

    # ---- intraday (orb or level)
    if not s:
        doc.update({"state": "closed", "why": "no NSE session today (%s)" % (cal.closed_reason(now.date()) or "closed")})
        return doc
    if now < s[0]:
        doc.update({"state": "not_open", "why": "NSE opens at %s IST" % m.clock(s[0])})
        return doc
    n_min = a.or_minutes or intra["or_minutes"]
    n_range = n_min // 5
    bars = _today(m.intraday_bars(src.chart(sym, "1d", "5m"), cal, now)[0], now.date())
    if [b["slot"] for b in bars] != list(range(len(bars))):
        doc.update({"state": "unusable", "why": "a 5-minute bar is missing today (halt or thin trading)"})
        return doc
    if len(bars) < n_range:
        doc.update({"state": "range_forming", "why": "the %d-minute range ends at %s IST" %
                    (n_min, m.clock(s[0] + datetime.timedelta(minutes=n_min)))})
        return doc
    orh = max(b["h"] for b in bars[:n_range])
    orl = min(b["l"] for b in bars[:n_range])
    level = orh if setup == "orb" else a.level
    if level is None:
        raise m.MarketError("--setup level needs --level PRICE")
    avg_v = sum(b["v"] for b in bars[:n_range]) / float(n_range)
    min_vol = avg_v * a.min_volume_ratio if a.min_volume_ratio else None
    last = bars[-1]["c"]
    doc.update({"or_minutes": n_min, "or_high": round(orh, 2), "or_low": round(orl, 2), "level": round(level, 2),
                "last": round(last, 2), "range_bar_avg_volume": int(avg_v)})
    if min_vol:
        doc.update({"min_volume": int(min_vol), "min_volume_ratio": a.min_volume_ratio})
    trig = find_trigger(bars, level, n_range, min_vol)
    if trig:
        t_end = _end(trig)
        run = (last / trig["c"] - 1) * 100
        doc.update({"state": "triggered", "trigger_time": m.clock(t_end), "entry": round(trig["c"], 2),
                    "trigger_volume": trig["v"], "since_trigger_pct": round(run, 2),
                    "stale": run > STALE_AFTER_PCT or (now - t_end) > datetime.timedelta(minutes=30)})
        if t_end > late:
            doc.update({"state": "expired", "why": "it triggered at %s, after your no-entries time %s" %
                        (m.clock(t_end), m.clock(late))})
        return doc
    if now >= late:
        doc.update({"state": "expired", "why": "no close above %s by %s IST (no_entries_after)" % (m.price(level), m.clock(late))})
        return doc
    if len(bars) == n_range:
        doc.update({"state": "waiting", "why": "the range is set; the first bar after it completes at %s IST" %
                    m.clock(s[0] + datetime.timedelta(minutes=n_min + 5))})
    else:
        doc.update({"state": "waiting"})
    doc["distance_pct"] = round((level / last - 1) * 100, 2)
    return doc


def line(doc, now):
    sym, st = doc["symbol"], doc["state"]
    head = "%s %s IST  %s" % (now.strftime("%a %d %b"), m.clock(now), sym)
    if st == "triggered":
        if doc["mode"] == "swing":
            return "%s  TRIGGERED (swing): the bar ending %s traded at the trigger %s; fill about %s; now %s." % (
                head, doc["trigger_time"], m.price(doc["level"]), m.price(doc["entry"]), m.price(doc["last"]))
        msg = "%s  TRIGGERED at %s: the bar closed at %s, above the %s %s (range low %s). Now %s (%+.2f%% since)." % (
            head, doc["trigger_time"], m.price(doc["entry"]),
            "opening-range high" if doc["setup"] == "orb" else "level", m.price(doc["level"]), m.price(doc.get("or_low")),
            m.price(doc["last"]), doc["since_trigger_pct"])
        if doc.get("stale"):
            msg += " STALE: it has run or time has passed; the entry would be worse than the plan assumes."
        return msg
    if st == "triggered_earlier":
        return "%s  already triggered on %s (traded through %s, fill about %s); the swing setup has moved on. Rescan." % (
            head, doc["trigger_date"], m.price(doc["level"]), m.price(doc["entry"]))
    if st == "waiting" and doc.get("distance_pct", 0) < 0 and doc.get("min_volume"):
        return "%s  waiting: price %s is above %s, but no bar has closed above it on volume >= %s (%sx the range bars' average)." % (
            head, m.price(doc["last"]), m.price(doc["level"]), "{:,}".format(doc["min_volume"]), doc["min_volume_ratio"])
    if st == "waiting" and doc["mode"] == "swing":
        extra = (" " + doc["why"] + ".") if doc.get("why") else ""
        return "%s  waiting: last %s, swing trigger %s (%+.2f%% away): a trade at the trigger is the entry.%s" % (
            head, m.price(doc["last"]), m.price(doc["level"]), doc["distance_pct"], extra)
    if st == "waiting":
        extra = (" " + doc["why"] + ".") if doc.get("why") else ""
        return "%s  waiting: last %s, needs a 5-minute close above %s (%+.2f%% away).%s" % (
            head, m.price(doc["last"]), m.price(doc["level"]), doc["distance_pct"], extra)
    if st == "not_open" and doc["mode"] == "swing":
        return "%s  market not open. Swing trigger %s, last close %s (%+.2f%% away)." % (
            head, m.price(doc["level"]), m.price(doc["last"]), doc["distance_pct"])
    return "%s  %s: %s." % (head, st.replace("_", " "), doc.get("why", ""))


def run_check(a, now=None):
    cfg = m.load_config(a.trader_dir)
    cal = m.Calendar(cfg)
    now = now or m.now_ist(a.now)
    src = getattr(a, "_src", None) or m.Source(offline=a.offline, pause=0)
    a._src = src
    doc = check(a, cfg, cal, src, now)
    if not a.no_write:
        m.write_json(m.tpath("entry.json", a.trader_dir), doc)
    return doc, now


def main(argv=None):
    p = argparse.ArgumentParser(prog="entry.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    for name in ("check", "watch"):
        q = m.add_common(sub.add_parser(name))
        q.add_argument("symbol")
        q.add_argument("--setup", choices=("orb", "level", "swing"), default="orb")
        q.add_argument("--level", type=float)
        q.add_argument("--or-minutes", type=int)
        q.add_argument("--min-volume-ratio", type=float, help="trigger bar volume >= this x the range bars' average")
        q.add_argument("--max-days", type=int, default=3, help="swing: sessions after the signal before it expires")
        q.add_argument("--no-write", action="store_true")
        if name == "watch":
            q.add_argument("--every", type=int, default=60, help="seconds between checks")
            q.add_argument("--max-checks", type=int, default=400)
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 1
    a._src = None
    try:
        if a.cmd == "check":
            doc, now = run_check(a)
            print(json.dumps(doc, indent=2) if a.json else line(doc, now))
            return 0 if doc["state"] == "triggered" else 1
        now = m.now_ist(a.now)
        for _ in range(a.max_checks):
            doc, now = run_check(a, now)
            print(json.dumps(doc) if a.json else line(doc, now))
            sys.stdout.flush()
            if doc["state"] in ("triggered", "triggered_earlier", "expired", "closed", "unusable"):
                return 0 if doc["state"] == "triggered" else 1
            if a.now:                              # replay: step the pinned clock, no real waiting
                now += datetime.timedelta(seconds=a.every)
            else:
                m.SLEEP(a.every)
                now = m.now_ist()
        return 1
    except m.MarketError as e:
        return m.fail(e)


if __name__ == "__main__":
    sys.exit(main())
