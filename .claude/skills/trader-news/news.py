"""trader-news: what is moving your NSE stocks. Suggestions only.

  news.py [--tickers A,B | from trader/watchlist.json] [--hours 24] [--all]

For each stock: the move since the previous close, Yahoo Finance headlines,
and the company's BSE corporate announcements (results, board meetings,
auditor changes, pledges, orders...). Shows only items new since the last
check (--all shows everything in the window), newest first, in India time,
tags each one and flags the ones worth reading before any trade.
Writes trader/news.json.
"""
import argparse
import datetime
import email.utils
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indmkt as m  # noqa: E402

# tag -> (patterns, is_flag). Order matters: the first matching tags are shown first.
TAGS = [
    ("auditor", [r"auditor.*(resign|qualif|cessation)", r"(resign|cessation).*auditor"], True),
    ("default", [r"\bdefault", r"insolvency", r"\bnclt\b", r"\bibc\b", r"bankrupt", r"winding up"], True),
    ("pledge", [r"pledge", r"encumbrance", r"invocation"], True),
    ("regulatory", [r"\bsebi\b.*(order|penalt|notice|ban)", r"show[- ]cause", r"penalt", r"tax demand",
                    r"\braid\b", r"\bsearch (and|&) seizure", r"\benforcement directorate\b", r"\bcbi\b"], True),
    ("surveillance", [r"\basm\b", r"\bgsm\b", r"surveillance"], True),
    ("results_due", [r"board meeting.*(consider|approve).*result", r"intimation.*board meeting.*result"], False),
    ("results", [r"\bresults?\b", r"\bq[1-4]\b", r"quarter(ly)? (ended|earnings|numbers)", r"net profit",
                 r"\bprofit (rises|falls|jumps|drops|up|down)", r"\bearnings\b", r"\brevenue\b"], False),
    ("dividend", [r"dividend"], False),
    ("corporate_action", [r"\bbonus\b", r"\bsplit\b", r"sub-?division", r"record date", r"buy-?back", r"rights issue"], False),
    ("fundraise", [r"\bqip\b", r"preferential", r"fund ?rais", r"\bncds?\b", r"debenture"], False),
    ("deal", [r"acqui", r"merger", r"amalgamation", r"joint venture", r"\bstake\b", r"\border\b", r"\bcontract\b",
              r"\bbags\b", r"\bwins\b", r"\bdeal\b"], False),
    ("analyst", [r"upgrade", r"downgrade", r"target price", r"\brating\b", r"brokerage", r"initiates? coverage",
                 r"\bbuy call\b", r"\bsell call\b", r"\boverweight\b", r"\bunderweight\b"], False),
    ("management", [r"\bresign", r"\bappoint", r"\bceo\b", r"managing director", r"\bcfo\b", r"chairman"], False),
    ("insider", [r"\bsast\b", r"regulation 29", r"\breg\.? 29\b", r"insider", r"promoter.*(buy|sell|acqui|sale)"], False),
    ("block_deal", [r"block deal", r"bulk deal"], False),
    ("macro", [r"\brbi policy\b", r"repo rate", r"inflation", r"\bgdp\b", r"union budget", r"\bfii", r"\bdii"], False),
]
_COMPILED = [(t, [re.compile(p, re.I) for p in pats], flag) for t, pats, flag in TAGS]


def tag(text):
    tags, flag = [], False
    for t, pats, is_flag in _COMPILED:
        if any(p.search(text) for p in pats):
            if t == "management" and "auditor" in tags:
                continue
            tags.append(t)
            flag = flag or is_flag
    return tags, flag


# ---------------------------------------------------------------- sources

def yahoo_items(src, sym, since):
    txt = src.rss(sym)
    try:
        root = ET.fromstring(txt)
    except ET.ParseError:
        raise m.MarketError("Yahoo's headline feed for %s is not valid XML" % sym)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        try:
            dt = email.utils.parsedate_to_datetime(pub).astimezone(m.IST)
        except (TypeError, ValueError):
            continue
        if dt < since or not title:
            continue
        out.append({"id": "y:" + (it.findtext("guid") or link or title), "time": dt.isoformat(),
                    "source": "Yahoo", "title": title, "url": link})
    return out


def bse_code(src, sym, isin, cache):
    if sym in cache:
        return cache[sym]
    txt = src.bse_lookup(sym)
    code = None
    if isin:
        mm = re.search(re.escape(isin) + r"\s+(\d{6})", txt)
        code = mm.group(1) if mm else None
    if not code:
        mm = re.search(r"<\w+>%s</\w+>\s+\w+\s+(\d{6})" % re.escape(sym), txt)
        code = mm.group(1) if mm else None
    cache[sym] = code
    return code


def _bse_time(s):
    s = (s or "").split(".")[0]
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=m.IST)
    except ValueError:
        return None


def bse_items(src, code, since, now):
    doc = src.bse_announcements(code, since.date(), now.date())
    out = []
    for r in (doc or {}).get("Table") or []:
        dt = _bse_time(r.get("DissemDT") or r.get("NEWS_DT"))
        if not dt or dt < since:
            continue
        head = (r.get("HEADLINE") or "").strip()
        sub = (r.get("SUBCATNAME") or r.get("CATEGORYNAME") or "").strip()
        title = head or (r.get("NEWSSUB") or "").strip()
        if sub and sub.lower() not in title.lower():
            title = "%s: %s" % (sub, title)
        att = r.get("ATTACHMENTNAME")
        out.append({"id": "b:" + str(r.get("NEWSID") or title), "time": dt.isoformat(), "source": "BSE",
                    "title": title, "url": (m.BSE_ATTACH + att) if att else (r.get("NSURL") or ""),
                    "category": r.get("CATEGORYNAME")})
    return out


def move(src, sym, cal, now):
    ser = src.chart(sym, "5d", "1d")
    bars = m.daily_bars(ser, cal, now)
    meta = ser.get("meta") or {}
    s = cal.session(now.date())
    if s and s[0] <= now < s[1] and bars:
        last = None
        try:                      # the last completed 5-minute bar, so a replay (--now) is honest
            intra, _ = m.intraday_bars(src.chart(sym, "1d", "5m"), cal, now)
            today = [b for b in intra if b["date"] == now.date().isoformat()]
            last = today[-1]["c"] if today else None
        except m.MarketError:
            pass
        if last is None and meta.get("regularMarketPrice"):
            last = float(meta["regularMarketPrice"])
        if last is None:
            return None
        prev = bars[-1]["c"]
        what = "vs yesterday's close"
    elif len(bars) >= 2:
        last, prev = bars[-1]["c"], bars[-2]["c"]
        what = "on %s" % bars[-1]["date"]
    else:
        return None
    return {"price": round(last, 2), "prev_close": round(prev, 2), "pct": round((last / prev - 1) * 100, 2), "label": what}


# ---------------------------------------------------------------- main

def tickers_for(a):
    if a.tickers:
        return m.split_symbols(a.tickers)
    doc = m.read_json(m.tpath("watchlist.json", a.trader_dir))
    if doc and doc.get("hits"):
        return [m.clean_symbol(h["symbol"]) for h in doc["hits"]][:a.max]
    raise m.MarketError("No tickers. Pass --tickers RELIANCE,TCS or run /trader-scanner first.")


def isin_map(tdir):
    import csv
    path = m.tpath("universe.csv", tdir)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {r["symbol"]: r.get("isin", "") for r in csv.DictReader(fh)}


def run(a):
    cfg = m.load_config(a.trader_dir)
    cal = m.Calendar(cfg)
    now = m.now_ist(a.now)
    since = now - datetime.timedelta(hours=a.hours)
    syms = tickers_for(a)
    src = m.Source(offline=a.offline, pause=0.2)
    codes_path = m.tpath("bse_codes.json", a.trader_dir)
    codes = m.read_json(codes_path, {}) or {}
    seen_path = m.tpath("news_seen.json", a.trader_dir)
    seen = set(m.read_json(seen_path, []) or [])
    isins = isin_map(a.trader_dir)
    report, problems = [], []
    for s in syms:
        entry = {"symbol": s, "move": None, "items": [], "new": 0}
        try:
            entry["move"] = move(src, s, cal, now)
        except m.MarketError as e:
            problems.append("%s price: %s" % (s, e))
        items = []
        if not a.no_yahoo:
            try:
                items += yahoo_items(src, s, since)
            except m.MarketError as e:
                problems.append("%s headlines: %s" % (s, e))
        if not a.no_bse:
            try:
                code = bse_code(src, s, isins.get(s), codes)
                if code:
                    items += bse_items(src, code, since, now)
                else:
                    problems.append("%s: no BSE listing found (NSE-only stock?)" % s)
            except m.MarketError as e:
                problems.append("%s BSE announcements: %s" % (s, e))
        for it in items:
            it["tags"], it["flag"] = tag(it["title"])
            it["new"] = it["id"] not in seen
        items.sort(key=lambda x: x["time"], reverse=True)
        entry["items"] = items
        entry["new"] = sum(1 for it in items if it["new"])
        report.append(entry)
    doc = {"as_of": now.isoformat(), "hours": a.hours, "stocks": report, "problems": problems}
    if not a.no_write:
        m.write_json(codes_path, codes)
        m.write_json(m.tpath("news.json", a.trader_dir), doc)
        for e in report:
            seen.update(it["id"] for it in e["items"])
        m.write_json(seen_path, sorted(seen)[-5000:])
    return doc


def render(doc, show_all):
    now = datetime.datetime.fromisoformat(doc["as_of"])
    out = ["News, %s IST, last %d hours%s:" % (now.strftime("%a %d %b %Y %H:%M"), doc["hours"],
                                                "" if show_all else " (new since your last check)")]
    any_flag = False
    for e in doc["stocks"]:
        mv = e["move"]
        head = "%-12s" % e["symbol"]
        if mv:
            head += " %s  %+.2f%% %s" % (m.inr(mv["price"]), mv["pct"], mv["label"])
        out.append(head)
        items = e["items"] if show_all else [i for i in e["items"] if i["new"]]
        if not items:
            out.append("    nothing new" if not show_all else "    nothing in this window")
            continue
        for it in items:
            dt = datetime.datetime.fromisoformat(it["time"])
            if dt.date() == now.date():
                when = m.clock(dt)
            elif (now.date() - dt.date()).days < 6:
                when = dt.strftime("%a %H:%M")
            else:
                when = dt.strftime("%d %b")
            tags = ",".join(it["tags"]) or "other"
            mark = "!" if it["flag"] else " "
            any_flag = any_flag or it["flag"]
            out.append("  %s %-9s %-5s [%s] %s" % (mark, when, it["source"], tags, m.clip(it["title"], 110)))
            if it.get("url") and it["source"] == "BSE":
                out.append("              %s" % it["url"])
    if any_flag:
        out.append("! = read this before any trade: auditor exits, defaults, pledges, regulator orders, surveillance.")
    shown = [i for e in doc["stocks"] for i in e["items"] if show_all or i["new"]]
    if any("results_due" in i["tags"] or "results" in i["tags"] for i in shown):
        out.append("Results in the window: a day trade is flat before the close; a swing trade held through results can gap either way.")
    for p in doc["problems"]:
        out.append("note: " + p)
    return "\n".join(out)


def main(argv=None):
    p = m.add_common(argparse.ArgumentParser(prog="news.py", description=__doc__.split("\n")[0]))
    p.add_argument("--tickers", help="comma-separated NSE symbols (default: trader/watchlist.json hits)")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--all", action="store_true", help="show everything in the window, not only new items")
    p.add_argument("--max", type=int, default=10, help="most watchlist symbols to check")
    p.add_argument("--no-bse", action="store_true")
    p.add_argument("--no-yahoo", action="store_true")
    p.add_argument("--no-write", action="store_true")
    a = p.parse_args(argv)
    try:
        doc = run(a)
    except m.MarketError as e:
        return m.fail(e)
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(doc, a.all) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
