"""trader-alerts: tell me when a price or a time is hit. Each alert rings once.

  alerts.py status                              is NSE open, when is the next session
  alerts.py add --from-plan                     stop, target and flat-by (or max-hold) from trader/plan.json
  alerts.py add SYMBOL --above 1020 | --below 995 | --time 15:10 [--note TEXT]
  alerts.py add --open-bell                     ring at the next NSE open
  alerts.py check                               ring whatever has been hit since it was set
  alerts.py list | remove ID | clear

A price alert rings when any completed 5-minute bar since it was set traded
through the level (its high for --above, its low for --below), so a quick
spike between checks is not missed. Data is free Yahoo bars: minutes old, not
tick by tick; your broker's own alerts are faster. Writes trader/alerts.json.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indmkt as m  # noqa: E402


def load(tdir):
    return m.read_json(m.tpath("alerts.json", tdir), {"next_id": 1, "alerts": []}) or {"next_id": 1, "alerts": []}


def save(tdir, doc):
    m.write_json(m.tpath("alerts.json", tdir), doc)


def new(doc, now, **kw):
    a = {"id": doc["next_id"], "created": now.isoformat(), "status": "active"}
    a.update(kw)
    doc["next_id"] += 1
    doc["alerts"].append(a)
    return a


def describe(a):
    if a["kind"] == "above":
        return "%s trades at or above %s" % (a["symbol"], m.price(a["level"]))
    if a["kind"] == "below":
        return "%s trades at or below %s" % (a["symbol"], m.price(a["level"]))
    if a["kind"] == "time":
        return "%s %s IST%s" % (a["date"], a["time"], " (%s)" % a["symbol"] if a.get("symbol") else "")
    return a["kind"]


def cmd_add(a, cfg, cal, now):
    doc = load(a.trader_dir)
    made = []
    if a.from_plan:
        p = m.read_json(m.tpath("plan.json", a.trader_dir))
        if not p:
            raise m.MarketError("No trader/plan.json. Run /trader-exit first.")
        s = p["symbol"]
        made.append(new(doc, now, symbol=s, kind="below", level=p["stop"], note="STOP for the %s plan" % p["mode"]))
        made.append(new(doc, now, symbol=s, kind="above", level=p["target"], note="TARGET for the %s plan" % p["mode"]))
        if p["mode"] == "intraday":
            made.append(new(doc, now, symbol=s, kind="time", date=p["trade_date"], time=p["flat_by"],
                            note="FLAT BY: close any %s position still open" % s))
        else:
            d, left = datetime.date.fromisoformat(p["trade_date"]), p.get("max_hold_days", 10)
            while left > 0:
                d += datetime.timedelta(days=1)
                if cal.session(d):
                    left -= 1
            made.append(new(doc, now, symbol=s, kind="time", date=d.isoformat(), time="15:00",
                            note="MAX HOLD: %d sessions are up for %s" % (p.get("max_hold_days", 10), s)))
    elif a.open_bell:
        o = cal.next_session(now)[0]
        made.append(new(doc, now, symbol="", kind="time", date=o.date().isoformat(), time=m.clock(o), note="NSE opening bell"))
    else:
        if not a.symbol:
            raise m.MarketError("Which stock? alerts.py add SYMBOL --above PRICE")
        s = m.clean_symbol(a.symbol)
        if a.above is not None:
            made.append(new(doc, now, symbol=s, kind="above", level=a.above, note=a.note or ""))
        if a.below is not None:
            made.append(new(doc, now, symbol=s, kind="below", level=a.below, note=a.note or ""))
        if a.time:
            t = m.parse_hhmm(a.time, "--time")
            d = now.date() if t > now.time() else now.date() + datetime.timedelta(days=1)
            made.append(new(doc, now, symbol=s, kind="time", date=d.isoformat(), time=m.clock(t), note=a.note or ""))
        if not made:
            raise m.MarketError("Give --above, --below or --time")
    save(a.trader_dir, doc)
    return ["Set #%d: %s%s" % (x["id"], describe(x), "  [%s]" % x["note"] if x.get("note") else "") for x in made]


def cmd_check(a, cfg, cal, now):
    doc = load(a.trader_dir)
    src = m.Source(offline=a.offline, pause=0.2)
    rung, problems, cache = [], [], {}
    for al in doc["alerts"]:
        if al["status"] != "active":
            continue
        if al["kind"] == "time":
            when = datetime.datetime.combine(datetime.date.fromisoformat(al["date"]), m.parse_hhmm(al["time"]), m.IST)
            if now >= when:
                al.update({"status": "rung", "rung_at": now.isoformat()})
                rung.append(al)
            continue
        s = al["symbol"]
        if s not in cache:
            try:
                cache[s] = m.intraday_bars(src.chart(s, "5d", "5m"), cal, now)[0]
            except m.MarketError as e:
                cache[s] = None
                problems.append("%s: %s" % (s, e))
        bars = cache[s]
        if not bars:
            continue
        since = datetime.datetime.fromisoformat(al["created"])
        for b in bars:
            end = m.from_ts(b["ts"]) + datetime.timedelta(minutes=5)
            if end <= since:
                continue
            hit = b["h"] >= al["level"] if al["kind"] == "above" else b["l"] <= al["level"]
            if hit:
                al.update({"status": "rung", "rung_at": end.isoformat(), "rung_bar": b["time"],
                           "rung_price": b["h"] if al["kind"] == "above" else b["l"]})
                rung.append(al)
                break
        if al["status"] == "active":
            al["last"] = bars[-1]["c"]
    save(a.trader_dir, doc)
    out = []
    for al in rung:
        when = datetime.datetime.fromisoformat(al["rung_at"])
        extra = " (the %s bar reached %s)" % (al["rung_bar"], m.price(al["rung_price"])) if al.get("rung_bar") else ""
        out.append("RING #%d at %s IST: %s%s%s" % (al["id"], m.clock(when), describe(al), extra,
                                                  "  [%s]" % al["note"] if al.get("note") else ""))
    active = [x for x in doc["alerts"] if x["status"] == "active"]
    if not rung:
        out.append("Nothing hit as of %s IST. %d alert%s waiting." % (m.clock(now), len(active), "" if len(active) == 1 else "s"))
    for p in problems:
        out.append("note: " + p)
    return out, rung


def main(argv=None):
    p = argparse.ArgumentParser(prog="alerts.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    m.add_common(sub.add_parser("status"))
    q = m.add_common(sub.add_parser("add"))
    q.add_argument("symbol", nargs="?")
    q.add_argument("--above", type=float)
    q.add_argument("--below", type=float)
    q.add_argument("--time")
    q.add_argument("--note")
    q.add_argument("--from-plan", action="store_true")
    q.add_argument("--open-bell", action="store_true")
    m.add_common(sub.add_parser("check"))
    m.add_common(sub.add_parser("list"))
    r = m.add_common(sub.add_parser("remove"))
    r.add_argument("id", type=int)
    m.add_common(sub.add_parser("clear"))
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 1
    try:
        cfg = m.load_config(a.trader_dir)
        cal = m.Calendar(cfg)
        now = m.now_ist(a.now)
        if a.cmd == "status":
            state, msg = cal.status(now)
            print(json.dumps({"state": state, "message": msg}) if a.json else msg)
            return 0 if state == "open" else 1
        if a.cmd == "add":
            print("\n".join(cmd_add(a, cfg, cal, now)))
            return 0
        if a.cmd == "check":
            lines, rung = cmd_check(a, cfg, cal, now)
            print(json.dumps(rung, indent=2) if a.json else "\n".join(lines))
            return 0 if rung else 1
        doc = load(a.trader_dir)
        if a.cmd == "list":
            if a.json:
                print(json.dumps(doc["alerts"], indent=2))
            elif not doc["alerts"]:
                print("No alerts.")
            for x in [] if a.json else doc["alerts"]:
                print("#%-3d %-6s %s%s" % (x["id"], x["status"], describe(x), "  [%s]" % x["note"] if x.get("note") else ""))
            return 0
        if a.cmd == "remove":
            before = len(doc["alerts"])
            doc["alerts"] = [x for x in doc["alerts"] if x["id"] != a.id]
            save(a.trader_dir, doc)
            print("Removed #%d." % a.id if len(doc["alerts"]) < before else "No alert #%d." % a.id)
            return 0
        if a.cmd == "clear":
            n = sum(1 for x in doc["alerts"] if x["status"] != "active")
            doc["alerts"] = [x for x in doc["alerts"] if x["status"] == "active"]
            save(a.trader_dir, doc)
            print("Cleared %d rung alert%s." % (n, "" if n == 1 else "s"))
            return 0
    except m.MarketError as e:
        return m.fail(e)
    return 1


if __name__ == "__main__":
    sys.exit(main())
