"""trader-journal: keep score of every suggestion, taken or not.

  journal.py log                    record the current suggestion (plan + sizing + odds)
  journal.py take ID [--price P] [--qty N]   you took it (at your real fill)
  journal.py skip ID                you passed (it is still scored, so you learn what skipping cost or saved)
  journal.py close ID --price P     your real exit, for a trade you took
  journal.py review                 work out what every open suggestion actually did, from the bars
  journal.py stats                  the real hit rate and average R after costs, by mode, setup and
                                    evidence level, and how the time machine's odds compared with reality
  journal.py list

This is how you find out how accurate the suggestions really are: not from the
backtest, from what happened after each one. Writes trader/journal.json.
"""
import argparse
import datetime
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indmkt as m  # noqa: E402

SCORED = ("target", "stop", "time")


def load(tdir):
    return m.read_json(m.tpath("journal.json", tdir), {"next": 1, "entries": []}) or {"next": 1, "entries": []}


def save(tdir, doc):
    m.write_json(m.tpath("journal.json", tdir), doc)


def find(doc, ident):
    key = str(ident).upper().lstrip("J")
    for e in doc["entries"]:
        if e["id"].lstrip("J") == key:
            return e
    raise m.MarketError("No journal entry %s (journal.py list)" % ident)


def cmd_log(a, now):
    tdir = a.trader_dir
    plan = m.read_json(m.tpath("plan.json", tdir))
    if not plan:
        raise m.MarketError("No trader/plan.json to log. Run /trader-exit (and ideally /trader-budget) first.")
    sz = m.read_json(m.tpath("sizing.json", tdir)) or {}
    od = m.read_json(m.tpath("odds.json", tdir)) or {}
    same = lambda d: d.get("symbol") == plan["symbol"] and d.get("entry", (d.get("plan") or {}).get("entry")) == plan["entry"]
    sz = sz if same(sz) else {}
    od = od if same(od) else {}
    doc = load(tdir)
    for e in doc["entries"]:
        if e["symbol"] == plan["symbol"] and e["entry"] == plan["entry"] and e["trade_date"] == plan["trade_date"]:
            return e, False
    sim, hist = od.get("simulation") or {}, od.get("history") or {}
    e = {"id": "J%d" % doc["next"], "logged_at": now.isoformat(), "symbol": plan["symbol"], "mode": plan["mode"],
         "product": plan["product"], "setup": (od.get("plan") or {}).get("setup_name") or plan.get("setup"),
         "trade_date": plan["trade_date"], "entry_state": plan.get("entry_state"), "trigger_time": plan.get("trigger_time"),
         "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"], "r_multiple": plan["r_multiple"],
         "flat_by": plan.get("flat_by"), "max_hold_days": plan.get("max_hold_days"),
         "qty": sz.get("qty"), "loss_at_stop": sz.get("loss_at_stop"), "gain_at_target": sz.get("gain_at_target"),
         "net_rr": sz.get("net_rr"), "suggestion": sz.get("suggestion"), "evidence": od.get("evidence"),
         "sim_p_target": sim.get("p_target"), "sim_p_stop": sim.get("p_stop"), "sim_avg_r_net": sim.get("avg_r_net"),
         "hist_n": hist.get("n"), "hist_avg_r_net": hist.get("avg_r_net"),
         "taken": None, "status": "open", "outcome": None}
    doc["next"] += 1
    doc["entries"].append(e)
    save(tdir, doc)
    return e, True


# ---------------------------------------------------------------- resolving outcomes from the bars

def resolve_intraday(e, src, cal, cfg, now):
    d = datetime.date.fromisoformat(e["trade_date"])
    s = cal.session(d)
    if not s:
        return {"outcome": "no_session"}
    bars, _ = m.intraday_bars(src.chart(e["symbol"], "5d" if (now.date() - d).days < 5 else "60d", "5m"), cal, now)
    bars = [b for b in bars if b["date"] == e["trade_date"]]
    flat = datetime.datetime.combine(d, m.parse_hhmm(e.get("flat_by") or cfg["intraday"]["flat_by"]), m.IST)
    late = datetime.datetime.combine(d, m.parse_hhmm(cfg["intraday"]["no_entries_after"]), m.IST)
    end = lambda b: m.from_ts(b["ts"]) + datetime.timedelta(minutes=5)
    if e.get("trigger_time"):
        t0 = datetime.datetime.combine(d, m.parse_hhmm(e["trigger_time"]), m.IST)
        after = [b for b in bars if end(b) > t0]
    else:
        logged = datetime.datetime.fromisoformat(e["logged_at"])
        fill = next((b for b in bars if end(b) > logged and end(b) <= late and b["h"] >= e["entry"]), None)
        if not fill:
            return {"outcome": "not_filled"} if now >= late else None
        after = [b for b in bars if b["ts"] > fill["ts"]]
    for b in after:
        if end(b) > flat:
            break
        if b["l"] <= e["stop"]:
            return {"outcome": "stop", "exit": e["stop"], "exit_time": m.clock(end(b))}
        if b["h"] >= e["target"]:
            return {"outcome": "target", "exit": e["target"], "exit_time": m.clock(end(b))}
    if now < flat:
        return None
    last = [b for b in after if end(b) <= flat]
    if not last:
        return {"outcome": "no_data"}
    return {"outcome": "time", "exit": last[-1]["c"], "exit_time": m.clock(end(last[-1]))}


def resolve_swing(e, src, cal, cfg, now):
    days = m.daily_bars(src.chart(e["symbol"], "6mo", "1d"), cal, now)
    td = e["trade_date"]
    if e.get("entry_state") == "triggered":
        start = next((i for i, b in enumerate(days) if b["date"] >= td), None)
        if start is None:
            return None
        e_i, entry = start, e["entry"]
    else:
        later = [i for i, b in enumerate(days) if b["date"] >= td][:3]
        e_i = next((i for i in later if days[i]["h"] >= e["entry"]), None)
        if e_i is None:
            return {"outcome": "not_filled"} if len(later) >= 3 else None
        entry = max(days[e_i]["o"], e["entry"])
    hold = e.get("max_hold_days") or cfg["swing"]["max_hold_days"]
    for j in range(e_i, min(len(days), e_i + hold)):
        b = days[j]
        if j > e_i and b["o"] <= e["stop"]:
            return {"outcome": "stop", "exit": b["o"], "exit_date": b["date"], "fill": entry}
        if j > e_i and b["o"] >= e["target"]:
            return {"outcome": "target", "exit": b["o"], "exit_date": b["date"], "fill": entry}
        if b["l"] <= e["stop"]:
            return {"outcome": "stop", "exit": e["stop"], "exit_date": b["date"], "fill": entry}
        if b["h"] >= e["target"]:
            return {"outcome": "target", "exit": e["target"], "exit_date": b["date"], "fill": entry}
    if e_i + hold > len(days):
        return None
    b = days[e_i + hold - 1]
    return {"outcome": "time", "exit": b["c"], "exit_date": b["date"], "fill": entry}


def score(e, res, cfg):
    e.update(res)
    if res["outcome"] not in SCORED:
        e["status"] = "resolved"
        return
    fill = res.get("fill", e["entry"])
    risk = fill - e["stop"]
    if risk <= 0:
        e["status"] = "resolved"
        e["outcome"] = "gapped_below_stop"
        return
    qty = e.get("qty") or 1
    costs = m.trade_costs(qty, fill, res["exit"], e["product"], cfg["costs"])["total"]
    e["r_gross"] = round((res["exit"] - fill) / risk, 3)
    e["r_net"] = round(e["r_gross"] - costs / (risk * qty), 3)
    e["status"] = "resolved"
    if e.get("taken") and e.get("actual_exit") is None:
        q = e.get("actual_qty") or qty
        f = e.get("actual_entry") or fill
        c = m.trade_costs(q, f, res["exit"], e["product"], cfg["costs"])["total"]
        e["pnl"] = round(q * (res["exit"] - f) - c, 2)
        e["pnl_basis"] = "rule-based exit (use close to record your real one)"


def cmd_review(a, now, cfg):
    cal = m.Calendar(cfg)
    src = m.Source(offline=a.offline, pause=0.2)
    doc = load(a.trader_dir)
    lines = []
    for e in doc["entries"]:
        if e["status"] != "open":
            continue
        try:
            res = (resolve_intraday if e["mode"] == "intraday" else resolve_swing)(e, src, cal, cfg, now)
        except m.MarketError as err:
            lines.append("%s %s: could not check (%s)" % (e["id"], e["symbol"], err))
            continue
        if res is None:
            lines.append("%s %s: still running" % (e["id"], e["symbol"]))
            continue
        score(e, res, cfg)
        if e["outcome"] in SCORED:
            lines.append("%s %s %s: %s at %s, %+.2fR after costs%s" % (
                e["id"], e["symbol"], e["trade_date"], e["outcome"], m.price(e["exit"]), e["r_net"],
                ("; you %s it" % ("took" if e["taken"] else "skipped")) if e["taken"] is not None else ""))
        else:
            lines.append("%s %s: %s" % (e["id"], e["symbol"], e["outcome"].replace("_", " ")))
    save(a.trader_dir, doc)
    return lines or ["Nothing open to review."]


# ---------------------------------------------------------------- stats

def ci(rs, seed=19, n=2000):
    if len(rs) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choice(rs) for _ in rs) for _ in range(n))
    return (means[int(0.025 * n)], means[int(0.975 * n) - 1])


def group_line(name, es):
    rs = [e["r_net"] for e in es]
    c = ci(rs)
    return "  %-26s %3d  target %3d  stop %3d  time %3d  win %4.0f%%  avg %+.2fR%s" % (
        name, len(es), sum(e["outcome"] == "target" for e in es), sum(e["outcome"] == "stop" for e in es),
        sum(e["outcome"] == "time" for e in es), 100.0 * sum(r > 0 for r in rs) / len(rs), statistics.mean(rs),
        "  (95%% %+.2f to %+.2f)" % c if c else "")


def cmd_stats(a):
    doc = load(a.trader_dir)
    done = [e for e in doc["entries"] if e.get("outcome") in SCORED]
    allr = doc["entries"]
    out = ["Journal: %d suggestions logged, %d scored, %d not filled, %d still open." % (
        len(allr), len(done), sum(1 for e in allr if e.get("outcome") == "not_filled"),
        sum(1 for e in allr if e["status"] == "open"))]
    if not done:
        out.append("Nothing scored yet. Log suggestions (journal.py log) and run journal.py review after they play out.")
        return out, {}
    out.append("All scored, after costs:")
    out.append(group_line("everything", done))
    groups = {}
    for e in done:
        groups.setdefault("%s / %s" % (e["mode"], e.get("setup") or "?"), []).append(e)
    out.append("By mode and setup:")
    out += [group_line(k, v) for k, v in sorted(groups.items())]
    ev = {}
    for e in done:
        ev.setdefault(e.get("evidence") or "none", []).append(e)
    out.append("By the time machine's evidence label (does the label mean anything?):")
    out += [group_line(k, v) for k, v in sorted(ev.items())]
    took = [e for e in done if e.get("taken") is True]
    skipped = [e for e in done if e.get("taken") is False]
    if took or skipped:
        out.append("Your discretion:")
        if took:
            out.append(group_line("taken", took))
        if skipped:
            out.append(group_line("skipped", skipped))
        pnl = [e["pnl"] for e in took if e.get("pnl") is not None]
        if pnl:
            out.append("  Rupees on trades you took: %s over %d trades." % (m.inr(sum(pnl)), len(pnl)))
    pred = [e for e in done if e.get("sim_p_target") is not None]
    if pred:
        p_hat = statistics.mean(e["sim_p_target"] for e in pred)
        p_real = sum(e["outcome"] == "target" for e in pred) / float(len(pred))
        s_hat = statistics.mean(e["sim_p_stop"] for e in pred)
        s_real = sum(e["outcome"] == "stop" for e in pred) / float(len(pred))
        out.append("Calibration over %d: simulation said target first %.0f%%, reality %.0f%%; stop first %.0f%%, reality %.0f%%."
                   % (len(pred), p_hat * 100, p_real * 100, s_hat * 100, s_real * 100))
    if len(done) < 30:
        out.append("Fewer than 30 scored: these numbers can still swing a lot. Keep logging.")
    return out, {"scored": len(done)}


def main(argv=None):
    p = argparse.ArgumentParser(prog="journal.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    m.add_common(sub.add_parser("log"))
    t = m.add_common(sub.add_parser("take"))
    t.add_argument("id")
    t.add_argument("--price", type=float)
    t.add_argument("--qty", type=int)
    s = m.add_common(sub.add_parser("skip"))
    s.add_argument("id")
    c = m.add_common(sub.add_parser("close"))
    c.add_argument("id")
    c.add_argument("--price", type=float, required=True)
    m.add_common(sub.add_parser("review"))
    m.add_common(sub.add_parser("stats"))
    m.add_common(sub.add_parser("list"))
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 1
    try:
        cfg = m.load_config(a.trader_dir)
        now = m.now_ist(a.now)
        if a.cmd == "log":
            e, fresh = cmd_log(a, now)
            print("%s %s: %s %s, entry %s, stop %s, target %s%s." % (
                "Logged" if fresh else "Already logged as", e["id"], e["symbol"], e["mode"], m.price(e["entry"]),
                m.price(e["stop"]), m.price(e["target"]),
                ", evidence %s" % e["evidence"] if e.get("evidence") else ""))
            if fresh:
                print("Tell me if you take it (journal.py take %s --price YOUR_FILL) or skip it. Either way it gets scored." % e["id"])
            return 0
        doc = load(a.trader_dir)
        if a.cmd in ("take", "skip", "close"):
            e = find(doc, a.id)
            if a.cmd == "take":
                e.update({"taken": True, "taken_at": now.isoformat(), "actual_entry": a.price or e["entry"],
                          "actual_qty": a.qty or e.get("qty")})
                msg = "%s marked taken at %s." % (e["id"], m.price(e["actual_entry"]))
            elif a.cmd == "skip":
                e["taken"] = False
                msg = "%s marked skipped. It is still scored, so you can see what skipping saved or cost." % e["id"]
            else:
                if not e.get("taken"):
                    raise m.MarketError("%s was not marked taken (journal.py take %s first)" % (e["id"], e["id"]))
                q, f = e.get("actual_qty") or e.get("qty") or 1, e.get("actual_entry") or e["entry"]
                c = m.trade_costs(q, f, a.price, e["product"], cfg["costs"])["total"]
                e.update({"actual_exit": a.price, "closed_at": now.isoformat(),
                          "pnl": round(q * (a.price - f) - c, 2), "pnl_basis": "your exit"})
                msg = "%s closed at %s: %s after charges." % (e["id"], m.price(a.price), m.inr(e["pnl"]))
            save(a.trader_dir, doc)
            print(msg)
            return 0
        if a.cmd == "review":
            print("\n".join(cmd_review(a, now, cfg)))
            return 0
        if a.cmd == "stats":
            lines, info = cmd_stats(a)
            print(json.dumps(info) if a.json else "\n".join(lines))
            return 0
        if a.cmd == "list":
            if not doc["entries"]:
                print("Journal is empty.")
            for e in doc["entries"]:
                print("%-4s %s %-10s %-8s entry %8s  %-10s %s%s" % (
                    e["id"], e["trade_date"], e["symbol"], e["mode"], m.price(e["entry"]),
                    e.get("outcome") or e["status"], "%+.2fR" % e["r_net"] if e.get("r_net") is not None else "",
                    {True: "  taken", False: "  skipped"}.get(e.get("taken"), "")))
            return 0
    except m.MarketError as e:
        return m.fail(e)
    return 1


if __name__ == "__main__":
    sys.exit(main())
