"""trader-budget: how many shares, what it costs, and does it pass your rules.

  size.py [--capital 100000] [--max-loss 500]

Reads trader/plan.json (trader-exit), and when present trader/odds.json
(trader-timemachine), trader/news.json (trader-news) and trader/journal.json
(trader-journal). Finds the most whole shares that fit all of:
  - the loss at the stop, INCLUDING every charge, within your max loss per trade
  - one position at most position_cap_pct of capital
  - the cash you have after your buffer
Then shows the rupees at the stop and at the target after STT, brokerage,
exchange charges, SEBI fee, GST, stamp duty (and DP charge for delivery), the
reward:risk after costs, the breakeven price, and a list of rule checks.
Writes trader/sizing.json. It never places an order: the checks are advice.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indmkt as m  # noqa: E402


def loss_at_stop(q, plan, costs):
    c = m.trade_costs(q, plan["entry"], plan["stop"], plan["product"], costs)
    return q * (plan["entry"] - plan["stop"]) + c["total"], c


def gain_at_target(q, plan, costs):
    c = m.trade_costs(q, plan["entry"], plan["target"], plan["product"], costs)
    return q * (plan["target"] - plan["entry"]) - c["total"], c


def size(plan, cfg, capital, max_loss):
    """Most whole shares that fit every limit, and which limit was the tightest."""
    entry, risk = plan["entry"], plan["entry"] - plan["stop"]
    lev = cfg["intraday_leverage"] if plan["mode"] == "intraday" else 1.0
    q_loss = int(max_loss // risk) + 1
    while q_loss > 0 and loss_at_stop(q_loss, plan, cfg["costs"])[0] > max_loss:
        q_loss -= 1
    caps = [
        ("max loss per trade %s, charges included" % m.inr(max_loss, 0), q_loss),
        ("position cap %g%% of capital" % cfg["position_cap_pct"],
         int(capital * cfg["position_cap_pct"] / 100.0 * lev // entry)),
        ("cash after your %g%% buffer" % cfg["cash_buffer_pct"],
         int(capital * (1 - cfg["cash_buffer_pct"] / 100.0) * lev // entry)),
    ]
    q = max(0, min(v for _, v in caps))
    return q, next(k for k, v in caps if v == q) if q > 0 else None


def journal_today(tdir, today):
    j = m.read_json(m.tpath("journal.json", tdir), {}) or {}
    taken = [e for e in j.get("entries", []) if e.get("taken") and (e.get("taken_at") or "")[:10] == today]
    realized = sum(e.get("pnl", 0) or 0 for e in taken if e.get("status") == "closed")
    open_risk = sum(e.get("loss_at_stop", 0) or 0 for e in taken if e.get("status") != "closed")
    return len(taken), realized, open_risk


def checks(plan, res, cfg, tdir, now):
    out = []

    def add(level, text):
        out.append({"level": level, "text": text})

    mn = cfg["min_net_reward_risk"]
    if res["net_rr"] >= mn:
        add("PASS", "reward:risk after costs %.2f, at or above your minimum %g" % (res["net_rr"], mn))
    else:
        add("SKIP", "reward:risk after costs is only %.2f (before costs %.2f), under your minimum %g"
            % (res["net_rr"], plan["r_multiple"], mn))
    cal = m.Calendar(cfg)
    if plan["mode"] == "intraday":
        s = cal.session(now.date())
        late = datetime.datetime.combine(now.date(), m.parse_hhmm(cfg["intraday"]["no_entries_after"]), m.IST)
        if not s:
            add("SKIP", "NSE is closed today (%s)" % (cal.closed_reason(now.date()) or "no session"))
        elif now >= late:
            add("SKIP", "it is past your no-entries time %s IST" % m.clock(late))
        else:
            add("PASS", "inside your entry window (until %s IST)" % m.clock(late))
    n, realized, open_risk = journal_today(tdir, now.date().isoformat())
    if n >= cfg["max_trades_per_day"]:
        add("SKIP", "you have taken %d trades today; your limit is %d" % (n, cfg["max_trades_per_day"]))
    else:
        add("PASS", "trades today %d of %d" % (n, cfg["max_trades_per_day"]))
    worst_day = -realized + open_risk + res["loss_at_stop"]
    if -realized >= cfg["daily_loss_limit"]:
        add("SKIP", "today's realized loss %s has reached your daily limit %s" % (m.inr(-realized), m.inr(cfg["daily_loss_limit"])))
    elif worst_day > cfg["daily_loss_limit"]:
        add("WARN", "if this and today's open trades all stop out, the day's loss is %s, over your %s limit"
            % (m.inr(worst_day), m.inr(cfg["daily_loss_limit"])))
    else:
        add("PASS", "worst case for the day %s, within your %s limit" % (m.inr(worst_day), m.inr(cfg["daily_loss_limit"])))
    uni = os.path.join(m.trader_dir(tdir), "universe.csv")
    if os.path.exists(uni):
        import csv
        with open(uni, encoding="utf-8") as fh:
            ser = {r["symbol"]: r.get("series") for r in csv.DictReader(fh)}
        if ser.get(plan["symbol"]) == "BE":
            add("SKIP" if plan["mode"] == "intraday" else "WARN",
                "trade-to-trade (BE) stock: delivery only, no intraday; thin and often in surveillance")
    odds = m.read_json(m.tpath("odds.json", tdir))
    if odds and odds.get("symbol") == plan["symbol"] and odds.get("plan", {}).get("entry") == plan["entry"]:
        ev = odds.get("evidence")
        h = odds.get("history", {})
        txt = "time machine evidence %s" % (ev or "?").upper()
        if h.get("n"):
            txt += " (history: %d trades, %+.2fR average after costs)" % (h["n"], h["avg_r_net"])
        if ev == "weak":
            txt += ": the odds lean against this plan"
        add({"supportive": "PASS", "mixed": "WARN", "weak": "SKIP"}.get(ev, "WARN"), txt)
    else:
        add("WARN", "no time-machine odds for this exact plan yet (run /trader-timemachine)")
    news = m.read_json(m.tpath("news.json", tdir))
    if news:
        st = next((x for x in news.get("stocks", []) if x["symbol"] == plan["symbol"]), None)
        if st:
            for it in st["items"]:
                if it.get("flag"):
                    add("WARN", "news flag: %s" % m.clip(it["title"], 100))
            if plan["mode"] == "swing" and any("results_due" in it["tags"] for it in st["items"]):
                add("WARN", "results are due: a swing trade held through them can gap past the stop")
            mv = st.get("move") or {}
            if mv.get("pct") is not None:
                for band in (5, 10, 20):
                    if band - 1.0 <= abs(mv["pct"]) < band:
                        add("WARN", "moved %+.1f%% today: may be near a %d%% circuit band, where orders stop filling"
                            % (mv["pct"], band))
                        break
    else:
        add("WARN", "no news check yet (run /trader-news %s)" % plan["symbol"])
    if plan.get("entry_state") == "triggered" and plan.get("warnings"):
        for w in plan["warnings"]:
            if "stale" in w:
                add("WARN", w)
    return out


def build(a):
    cfg = m.load_config(a.trader_dir)
    now = m.now_ist(a.now)
    plan = m.read_json(m.tpath("plan.json", a.trader_dir))
    if not plan:
        raise m.MarketError("No trader/plan.json. Run /trader-exit first.")
    capital = a.capital or cfg["capital"]
    max_loss = a.max_loss or cfg["max_loss_per_trade"]
    q, decided = size(plan, cfg, capital, max_loss)
    doc = {"symbol": plan["symbol"], "mode": plan["mode"], "product": plan["product"], "as_of": now.isoformat(),
           "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"],
           "capital": capital, "max_loss": max_loss, "qty": q, "decided_by": decided}
    if q <= 0:
        lev = cfg["intraday_leverage"] if plan["mode"] == "intraday" else 1.0
        pos_cap_rupees = capital * cfg["position_cap_pct"] / 100.0 * lev
        buf_rupees = capital * (1 - cfg["cash_buffer_pct"] / 100.0) * lev
        entry = plan["entry"]
        if int(pos_cap_rupees // entry) == 0:
            text = ("even 1 share costs %s, over your position cap (%g%% of %s = %s)"
                    % (m.inr(entry), cfg["position_cap_pct"], m.inr(capital, 0), m.inr(pos_cap_rupees)))
        elif int(buf_rupees // entry) == 0:
            text = ("even 1 share costs %s, over your cash limit (%s deployable after %g%% buffer)"
                    % (m.inr(entry), m.inr(buf_rupees), cfg["cash_buffer_pct"]))
        else:
            one = loss_at_stop(1, plan, cfg["costs"])[0]
            text = ("even 1 share loses %s at the stop (costs included), over your max loss %s"
                    % (m.inr(one), m.inr(max_loss)))
        doc.update({"checks": [{"level": "SKIP", "text": text}], "suggestion": "skip"})
        return doc
    loss, c_stop = loss_at_stop(q, plan, cfg["costs"])
    gain, c_tgt = gain_at_target(q, plan, cfg["costs"])
    c_flat = m.trade_costs(q, plan["entry"], plan["entry"], plan["product"], cfg["costs"])["total"]
    doc.update({"value": round(q * plan["entry"], 2), "pct_of_capital": round(q * plan["entry"] / capital * 100, 1),
                "loss_at_stop": round(loss, 2), "costs_at_stop": c_stop, "gain_at_target": round(gain, 2),
                "costs_at_target": c_tgt, "net_rr": round(gain / loss, 2) if loss > 0 else None,
                "breakeven": m.ceil_tick(plan["entry"] + c_flat / q, plan["symbol"], cfg)})
    doc["checks"] = checks(plan, doc, cfg, a.trader_dir, now)
    doc["suggestion"] = "skip" if any(c["level"] == "SKIP" for c in doc["checks"]) else (
        "consider, with care" if any(c["level"] == "WARN" for c in doc["checks"]) else "consider")
    return doc


def render(d):
    head = "Sizing %s (%s, %s): entry %s, stop %s, target %s" % (
        d["symbol"], d["mode"], "MIS" if d["product"] == "intraday" else "CNC delivery",
        m.price(d["entry"]), m.price(d["stop"]), m.price(d["target"]))
    out = [head]
    if d["qty"] > 0:
        out.append("  %d shares, %s (%.1f%% of %s), decided by: %s" % (
            d["qty"], m.inr(d["value"]), d["pct_of_capital"], m.inr(d["capital"], 0), d["decided_by"]))
        cs, ct = d["costs_at_stop"], d["costs_at_target"]
        out.append("  If the stop fills:   lose %s (%s price move + %s charges)" % (
            m.inr(d["loss_at_stop"]), m.inr(d["loss_at_stop"] - cs["total"]), m.inr(cs["total"])))
        out.append("  If the target fills: make %s (%s price move - %s charges)" % (
            m.inr(d["gain_at_target"]), m.inr(d["gain_at_target"] + ct["total"]), m.inr(ct["total"])))
        out.append("  Charges at the target: brokerage %s, STT %s, exchange %s, GST %s, stamp %s%s, SEBI %s." % (
            m.inr(ct["brokerage"]), m.inr(ct["stt"]), m.inr(ct["exchange"]), m.inr(ct["gst"]), m.inr(ct["stamp"]),
            ", DP %s" % m.inr(ct["dp"]) if ct["dp"] else "", m.inr(ct["sebi"])))
        out.append("  Reward:risk after costs %.2f. Breakeven %s (the price that just covers the charges)." % (
            d["net_rr"], m.price(d["breakeven"])))
    out.append("Checks:")
    for c in d["checks"]:
        out.append("  %-4s  %s" % (c["level"], c["text"]))
    if d["suggestion"] == "skip":
        out.append("Suggestion: SKIP, by your own rules. Your call; nothing is placed either way.")
    else:
        out.append("Suggestion: %s. Your call; nothing is placed. To track it: /trader-journal log." % d["suggestion"].upper())
    return "\n".join(out)


def main(argv=None):
    p = m.add_common(argparse.ArgumentParser(prog="size.py", description=__doc__.split("\n")[0]))
    p.add_argument("--capital", type=float)
    p.add_argument("--max-loss", type=float)
    p.add_argument("--no-write", action="store_true")
    a = p.parse_args(argv)
    try:
        doc = build(a)
    except m.MarketError as e:
        return m.fail(e)
    if not a.no_write:
        m.write_json(m.tpath("sizing.json", a.trader_dir), doc)
    print(json.dumps(doc, indent=2, ensure_ascii=False) if a.json else render(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
