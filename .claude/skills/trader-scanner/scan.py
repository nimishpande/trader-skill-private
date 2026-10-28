"""trader-scanner: find NSE charts that match a rule. Suggestions only.

  scan.py universe [--index nifty500|nifty200|nifty100|nifty50|all] [--file CSV]
        -> trader/universe.csv (the list the scans read)
  scan.py swing [--symbols A,B | --watchlist FILE] [--min-price 20] [--min-turnover-cr 2]
        -> daily setups for swing trades: 20-day breakout, bull flag, 20-DMA
           reclaim in an uptrend, oversold bounce. Best before 9:15 or after 15:30.
  scan.py orb [--at HH:MM] [--or-minutes 15]
        -> intraday opening-range breakouts: the first completed 5-minute bar
           that CLOSES above the high of the 9:15-9:30 range.

Writes trader/watchlist.json for trader-news, trader-entry and trader-suggest.
A hit means a chart matched a rule. It is not a buy signal.
"""
import argparse
import csv
import datetime
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indmkt as m  # noqa: E402

KEEP_SERIES = ("EQ", "BE")


# ---------------------------------------------------------------- universe

def _col(header, *names):
    norm = [h.strip().lower() for h in header]
    for n in names:
        if n in norm:
            return norm.index(n)
    return None


def parse_list(text):
    """NSE's index lists (ind_nifty500list.csv) and EQUITY_L.csv -> rows."""
    rdr = csv.reader(io.StringIO(text.lstrip("﻿")))
    rows = [r for r in rdr if r and any(x.strip() for x in r)]
    if not rows:
        raise m.MarketError("the NSE list is empty")
    h = rows[0]
    ci = {
        "symbol": _col(h, "symbol"),
        "name": _col(h, "company name", "name of company"),
        "series": _col(h, "series"),
        "isin": _col(h, "isin code", "isin number", "isin"),
        "industry": _col(h, "industry"),
    }
    if ci["symbol"] is None:
        raise m.MarketError("the NSE list has no Symbol column (got %s)" % ", ".join(h[:6]))
    out, skipped = [], {}
    for r in rows[1:]:
        def g(k):
            i = ci[k]
            return r[i].strip() if i is not None and i < len(r) else ""
        series = g("series").upper() or "EQ"
        if series not in KEEP_SERIES:
            skipped[series] = skipped.get(series, 0) + 1
            continue
        out.append({"symbol": g("symbol").upper(), "name": g("name"), "series": series,
                    "isin": g("isin"), "industry": g("industry")})
    return out, skipped


def write_universe(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "name", "series", "isin", "industry"])
        for r in rows:
            w.writerow([r["symbol"], r["name"], r["series"], r["isin"], r["industry"]])


def read_universe(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh)]


def cmd_universe(a):
    src = m.Source(offline=a.offline)
    if a.file:
        try:
            with open(a.file, encoding="utf-8-sig") as fh:
                text = fh.read()
        except OSError as e:
            return m.fail("Could not read %s: %s" % (a.file, e))
        origin = a.file
    else:
        try:
            text = src.nse_csv(a.index)
        except m.MarketError as e:
            return m.fail("Could not download the NSE %s list: %s\nFix: open %s in your browser, save it, "
                          "and run: scan.py universe --file <saved csv>" % (a.index, e, m.NSE_INDEX_CSV[a.index]))
        origin = m.NSE_INDEX_CSV[a.index]
    try:
        rows, skipped = parse_list(text)
    except m.MarketError as e:
        return m.fail(str(e))
    path = m.tpath("universe.csv", a.trader_dir)
    write_universe(path, rows)
    t2t = sum(1 for r in rows if r["series"] == "BE")
    msg = "Wrote %s: %d NSE stocks from %s" % (m.show_path(path), len(rows), origin)
    msg += " (%d trade-to-trade BE: delivery only, never intraday)" % t2t if t2t else ""
    if skipped:
        msg += ". Left out other series: " + ", ".join("%s %d" % kv for kv in sorted(skipped.items()))
    sys.stdout.write(msg + ".\n")
    return 0


# ---------------------------------------------------------------- symbols to scan

def symbols_for(a):
    """(symbols, series map, where they came from)."""
    uni = read_universe(m.tpath("universe.csv", a.trader_dir)) or []
    series = {r["symbol"]: r.get("series", "EQ") for r in uni}
    if a.symbols:
        return m.split_symbols(a.symbols), series, "your list"
    if a.watchlist:
        doc = m.read_json(a.watchlist)
        if isinstance(doc, dict) and "hits" in doc:
            syms = [m.clean_symbol(h["symbol"]) for h in doc["hits"]]
        else:
            with open(a.watchlist, encoding="utf-8") as fh:
                syms = m.split_symbols(fh.read().replace("\n", ","))
        return syms, series, os.path.basename(a.watchlist)
    if uni:
        return [r["symbol"] for r in uni], series, "trader/universe.csv"
    raise m.MarketError("No list to scan. Run: scan.py universe   (or pass --symbols RELIANCE,TCS)")


def progress(a, done, total):
    if not a.json and total > 40:
        sys.stderr.write("\r  fetched %d of %d" % (done, total))
        if done >= total:
            sys.stderr.write("\n")


# ---------------------------------------------------------------- swing (daily)

def swing_scan(src, syms, cal, now, cfg, min_price, min_turnover_cr, confirm, a):
    data, failed = src.spark(syms, "1y", "1d", progress=lambda d, t: progress(a, d, t))
    hits, liquid, short = [], 0, 0
    for s in syms:
        ser = data.get(s)
        if not ser:
            continue
        bars = m.daily_bars(ser, cal, now)
        if len(bars) < 22:
            short += 1
            continue
        meta = ser.get("meta") or {}
        last = bars[-1]
        px = last["c"]
        vol = meta.get("regularMarketVolume") or last["v"] or 0
        turnover_cr = px * vol / 1e7
        if px < min_price or turnover_cr < min_turnover_cr:
            continue
        liquid += 1
        closes = [b["c"] for b in bars]
        rsi = m.rsi_series(closes)
        i = len(closes) - 1
        found = m.setups_at(closes, i, rsi)
        if found:
            hits.append({"symbol": s, "setups": found, "date": last["date"], "close": round(px, 2),
                         "turnover_cr": round(turnover_cr, 2), "context": m.context_at(closes, i, rsi)})
    hits.sort(key=lambda h: -h["turnover_cr"])
    confirmed = 0
    for h in hits[:confirm] if confirm else hits:
        try:
            bars = m.daily_bars(src.chart(h["symbol"], "1y", "1d"), cal, now)
        except m.MarketError:
            continue
        atr = m.atr_series(bars)
        last = bars[-1]
        h["high"] = round(last["h"], 2)
        h["low"] = round(last["l"], 2)
        h["atr14"] = round(atr[-1], 2) if atr[-1] else None
        h["trigger"] = m.ceil_tick(last["h"] + m.tick_value(last["h"]), h["symbol"], cfg)
        h["note"] = "watch for a trade above the signal day's high, %s" % m.price(h["trigger"])
        confirmed += 1
    return {"fetched": len(syms) - len(failed), "failed": len(failed), "liquid": liquid,
            "too_short": short, "confirmed": confirmed, "hits": hits}


def cmd_swing(a):
    cfg = m.load_config(a.trader_dir)
    cal = m.Calendar(cfg)
    try:
        now = m.now_ist(a.now)
        syms, series, origin = symbols_for(a)
        src = m.Source(offline=a.offline)
        min_price = cfg["min_price"] if a.min_price is None else a.min_price
        min_to = cfg["swing"]["min_turnover_cr"] if a.min_turnover_cr is None else a.min_turnover_cr
        res = swing_scan(src, syms, cal, now, cfg, min_price, min_to, a.confirm, a)
    except m.MarketError as e:
        return m.fail(e)
    state, _ = cal.status(now)
    as_of = res["hits"][0]["date"] if res["hits"] else None
    doc = {"mode": "swing", "as_of": now.isoformat(), "data_through": as_of, "universe": origin,
           "scanned": len(syms), "fetched": res["fetched"], "failed": res["failed"], "liquid": res["liquid"],
           "min_price": min_price, "min_turnover_cr": min_to, "hits": res["hits"]}
    if not a.no_write:
        m.write_json(m.tpath("watchlist.json", a.trader_dir), doc)
    if a.json:
        print(json.dumps(doc, indent=2))
        return 0 if res["hits"] else 1
    out = ["Swing scan (daily closes), %s IST, from %s." % (now.strftime("%a %d %b %Y %H:%M"), origin)]
    if state == "open":
        out.append("NSE is open: today's bar is left out until 15:30, so this reads yesterday's close.")
    out.append("Fetched %d of %d charts%s; %d passed the liquidity floor (price >= %s, last session turnover >= %s crore)."
               % (res["fetched"], len(syms), " (%d failed or had no data)" % res["failed"] if res["failed"] else "",
                  res["liquid"], m.inr(min_price, 0), ("%g" % min_to)))
    if not res["hits"]:
        out.append("No chart matched a swing setup.")
    else:
        out.append("%d matched at least one setup (most traded first):" % len(res["hits"]))
        for h in res["hits"][:a.show]:
            ctx = h["context"]
            trend = "above 200-DMA" if ctx.get("above_200dma") else ("below 200-DMA" if ctx.get("above_200dma") is False else "no 200-DMA yet")
            out.append("  %-12s %11s  %-28s RSI %s, %s%s" % (
                h["symbol"], m.inr(h["close"]), ", ".join(m.SETUP_SHORT[s] for s in h["setups"]),
                "%.0f" % ctx["rsi14"] if ctx.get("rsi14") is not None else "-", trend,
                ", trigger %s" % m.price(h["trigger"]) if h.get("trigger") else ""))
        if len(res["hits"]) > a.show:
            out.append("  ... and %d more in trader/watchlist.json" % (len(res["hits"]) - a.show))
        if any(series.get(h["symbol"]) == "BE" for h in res["hits"]):
            out.append("BE-series hits are trade-to-trade: delivery only.")
    out.append("A setup is a rule matching on a chart, not a prediction. Next: pick one, then /trader-news and /trader-entry.")
    sys.stdout.write("\n".join(out) + "\n")
    return 0 if res["hits"] else 1


# ---------------------------------------------------------------- opening-range breakout (intraday)

def orb_scan(src, syms, cal, day_session, cutoff, n_range, cfg, min_price, confirm, a):
    data, failed = src.spark(syms, "1d", "5m", progress=lambda d, t: progress(a, d, t))
    open_dt = day_session[0]
    need = int((cutoff - open_dt).total_seconds() // 300)       # completed bars expected by the cutoff
    usable, cands, hits = [], [], []
    for s in syms:
        ser = data.get(s)
        if not ser:
            continue
        bars, _ = m.intraday_bars(ser, cal, cutoff)
        bars = [b for b in bars if b["date"] == open_dt.date().isoformat()]
        if len(bars) != need or [b["slot"] for b in bars] != list(range(need)):
            continue
        last = bars[-1]["c"]
        if last < min_price:
            continue
        usable.append(s)
        rng_close = max(b["c"] for b in bars[:n_range])
        if any(b["c"] >= rng_close for b in bars[n_range:]):
            vol = (ser.get("meta") or {}).get("regularMarketVolume") or 0     # spark has no per-bar volume
            cands.append((last * vol, s))
    cands.sort(reverse=True)
    todo = cands[:confirm] if confirm else cands
    for turnover, s in todo:
        try:
            bars, _ = m.intraday_bars(src.chart(s, "1d", "5m"), cal, cutoff)
        except m.MarketError:
            continue
        bars = [b for b in bars if b["date"] == open_dt.date().isoformat()]
        if len(bars) < n_range + 1:
            continue
        orh = max(b["h"] for b in bars[:n_range])
        orl = min(b["l"] for b in bars[:n_range])
        for b in bars[n_range:]:
            if b["c"] > orh:
                end = m.from_ts(b["ts"]) + datetime.timedelta(minutes=5)
                last = bars[-1]["c"]
                hits.append({"symbol": s, "hit_time": m.clock(end), "hit_close": round(b["c"], 2),
                             "or_high": round(orh, 2), "or_low": round(orl, 2),
                             "over_pct": round((b["c"] / orh - 1) * 100, 2),
                             "last": round(last, 2), "since_hit_pct": round((last / b["c"] - 1) * 100, 2),
                             "hit_bar_volume": b["v"],
                             "avg_range_bar_volume": int(sum(x["v"] for x in bars[:n_range]) / n_range),
                             "turnover_cr": round(sum(x["c"] * x["v"] for x in bars) / 1e7, 2)})
                break
    hits.sort(key=lambda h: (h["hit_time"], -h["turnover_cr"]))
    return {"fetched": len(syms) - len(failed), "failed": len(failed), "usable": len(usable),
            "candidates": len(cands), "confirmed": len(todo), "skipped": max(0, len(cands) - len(todo)), "hits": hits}


def cmd_orb(a):
    cfg = m.load_config(a.trader_dir)
    cal = m.Calendar(cfg)
    try:
        now = m.now_ist(a.now)
        n_min = a.or_minutes or cfg["intraday"]["or_minutes"]
        if n_min % 5:
            return m.fail("--or-minutes must be a multiple of 5")
        n_range = n_min // 5
        s = cal.session(now.date())
        if not s:
            return m.fail("No NSE session today (%s). Run the swing scan instead, or pin a day with --now." %
                          (cal.closed_reason(now.date()) or "closed"))
        cutoff = now
        if a.at:
            cutoff = datetime.datetime.combine(now.date(), m.parse_hhmm(a.at, "--at"), m.IST)
        cutoff = min(cutoff, s[1])
        first = s[0] + datetime.timedelta(minutes=n_min + 5)
        if cutoff < first:
            return m.fail("Too early: the %d-minute opening range ends at %s and the first bar after it completes at %s IST."
                          % (n_min, m.clock(s[0] + datetime.timedelta(minutes=n_min)), m.clock(first)))
        late = datetime.datetime.combine(now.date(), m.parse_hhmm(cfg["intraday"]["no_entries_after"]), m.IST)
        if cutoff > late and not a.at:
            return m.fail("After %s IST (your no_entries_after): an opening-range breakout this late is stale. "
                          "Use --at HH:MM to replay the morning." % m.clock(late))
        cutoff = cutoff.replace(second=0, microsecond=0)
        cutoff -= datetime.timedelta(minutes=(cutoff.minute - s[0].minute) % 5)   # back to the bar grid
        syms, series, origin = symbols_for(a)
        t2t = [x for x in syms if series.get(x) == "BE"]
        syms = [x for x in syms if series.get(x) != "BE"]
        src = m.Source(offline=a.offline)
        min_price = cfg["min_price"] if a.min_price is None else a.min_price
        if not a.json and len(syms) > 40:
            sys.stderr.write("Stage 1: 5-minute closes for %d symbols (%d requests)...\n" % (len(syms), (len(syms) + 19) // 20))
        res = orb_scan(src, syms, cal, s, cutoff, n_range, cfg, min_price, a.confirm, a)
    except m.MarketError as e:
        return m.fail(e)
    doc = {"mode": "orb", "as_of": cutoff.isoformat(), "or_minutes": n_min, "universe": origin,
           "scanned": len(syms), "fetched": res["fetched"], "failed": res["failed"], "usable": res["usable"],
           "candidates": res["candidates"], "confirmed": res["confirmed"], "skipped_partial": res["skipped"],
           "excluded_t2t": t2t, "hits": res["hits"]}
    if not a.no_write:
        m.write_json(m.tpath("watchlist.json", a.trader_dir), doc)
    if a.json:
        print(json.dumps(doc, indent=2))
        return 0 if res["hits"] else 1
    out = ["Opening-range breakout scan, %s %s IST: %d-minute range (%s to %s), 5-minute bars, Yahoo."
           % (cutoff.strftime("%a %d %b %Y"), m.clock(cutoff), n_min, m.clock(s[0]),
              m.clock(s[0] + datetime.timedelta(minutes=n_min)))]
    out.append("Fetched %d of %d charts%s; %d usable; %d candidates, %d confirmed with real highs and lows."
               % (res["fetched"], len(syms), " (%d failed or had no data)" % res["failed"] if res["failed"] else "",
                  res["usable"], res["candidates"], res["confirmed"]))
    if res["skipped"]:
        out.append("PARTIAL: %d more candidates not checked (--confirm %d; --confirm 0 checks all)." % (res["skipped"], a.confirm))
    if not res["hits"]:
        out.append("No usable chart closed above its opening-range high by %s." % m.clock(cutoff))
    else:
        out.append("%d of %d usable charts closed above their opening-range high by %s:"
                   % (len(res["hits"]), res["usable"], m.clock(cutoff)))
        for h in res["hits"][:a.show]:
            out.append("  %s  %-12s %11s  range %s to %s  %+.2f%% over  now %s (%+.2f%% since)" % (
                h["hit_time"], h["symbol"], m.inr(h["hit_close"]), m.price(h["or_low"]), m.price(h["or_high"]),
                h["over_pct"], m.price(h["last"]), h["since_hit_pct"]))
        if len(res["hits"]) > a.show:
            out.append("  ... and %d more in trader/watchlist.json" % (len(res["hits"]) - a.show))
        out.append("An early hit has had time to move on; a later hit is closer to the price now.")
    if t2t:
        out.append("Left out %d trade-to-trade (BE) stocks: no intraday allowed in them." % len(t2t))
    out.append("A breakout is a rule matching, not a prediction. Next: pick one, then /trader-news and /trader-entry.")
    sys.stdout.write("\n".join(out) + "\n")
    return 0 if res["hits"] else 1


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(prog="scan.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    u = m.add_common(sub.add_parser("universe", help="build trader/universe.csv from NSE's lists"))
    u.add_argument("--index", default="nifty500", choices=sorted(m.NSE_INDEX_CSV))
    u.add_argument("--file", help="a CSV you saved from NSE (index list or EQUITY_L.csv)")
    for name, h in (("swing", "daily setups for swing trades"), ("orb", "intraday opening-range breakouts")):
        q = m.add_common(sub.add_parser(name, help=h))
        g = q.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json or a text file of symbols")
        q.add_argument("--min-price", type=float)
        q.add_argument("--show", type=int, default=15)
        q.add_argument("--no-write", action="store_true")
        if name == "swing":
            q.add_argument("--min-turnover-cr", type=float)
            q.add_argument("--confirm", type=int, default=60, help="hits to re-check with highs/lows (0 = all)")
        else:
            q.add_argument("--at", help="cut the clock at HH:MM IST (replay the morning)")
            q.add_argument("--or-minutes", type=int)
            q.add_argument("--confirm", type=int, default=150, help="candidates to confirm (0 = all)")
    sub._name_parser_map["daily"] = sub._name_parser_map["swing"]       # alias
    a = p.parse_args(argv)
    if a.cmd is None:
        p.print_help()
        return 1
    if a.cmd == "universe":
        return cmd_universe(a)
    if a.cmd in ("swing", "daily"):
        return cmd_swing(a)
    return cmd_orb(a)


if __name__ == "__main__":
    sys.exit(main())
