"""trader-timemachine: the honest odds for a plan, before any rupee is risked.

  timemachine.py run [--replays 10000] [--seed 19] [--window 2] [--history 2y]

Reads trader/plan.json (from trader-exit). Two kinds of evidence:

1. Simulation. Intraday: the rest of the session is rebuilt 10,000 times, each
   5-minute bar drawn from this stock's own bars at that time of day over its
   last ~60 sessions. Swing: the holding period is rebuilt from its own daily
   bars over the last year, gaps included. Counts target first / stop first /
   neither.
2. History. The same rule (same setup, same stop rule, same R target, same exit
   time) replayed on every past day it fired on this stock, one trade at a time.

Both are scored in R (1R = the planned risk) after Indian trading costs, and the
history average comes with a 95% bootstrap range, so a lucky handful of trades
cannot pass for an edge. Writes trader/odds.json. A simulation is not a forecast.
"""
import argparse
import datetime
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indmkt as m  # noqa: E402


# ---------------------------------------------------------------- shared scoring

def boot_ci(rs, seed=19, n=2000):
    if len(rs) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choice(rs) for _ in rs) for _ in range(n))
    return [round(means[int(0.025 * n)], 3), round(means[int(0.975 * n) - 1], 3)]


def cost_r(entry, exit_px, risk, qty, product, costs):
    """Trading costs for this round trip, in R."""
    c = m.trade_costs(qty, entry, exit_px, product, costs)["total"]
    return c / (risk * qty)


def ref_qty(cfg, risk, entry):
    """The quantity trader-budget would roughly pick, so costs are scored at a realistic size."""
    q = int(cfg["max_loss_per_trade"] // max(risk, 0.01))
    cap = int(cfg["capital"] * cfg["position_cap_pct"] / 100.0 // entry)
    return max(1, min(q, cap) if cap > 0 else q)


def summarize(trades, seed):
    if not trades:
        return {"n": 0}
    rs = [t["r_net"] for t in trades]
    cnt = {k: sum(1 for t in trades if t["outcome"] == k) for k in ("target", "stop", "time")}
    return {"n": len(trades), "target": cnt["target"], "stop": cnt["stop"], "time": cnt["time"],
            "win_rate": round(sum(1 for r in rs if r > 0) / float(len(rs)), 3),
            "avg_r_gross": round(statistics.mean(t["r_gross"] for t in trades), 3),
            "avg_r_net": round(statistics.mean(rs), 3), "ci95_avg_r_net": boot_ci(rs, seed),
            "worst_r": round(min(rs), 2), "best_r": round(max(rs), 2), "trades": trades[-60:]}


def verdict(sim, hist):
    lines = []
    n = hist.get("n", 0)
    if n == 0:
        lines.append("History: the rule never fired on this stock in the window, so there is no track record.")
    else:
        ci = hist.get("ci95_avg_r_net")
        base = "History: fired %d times; %d hit the target, %d the stop, %d neither. Average %+.2fR after costs" % (
            n, hist["target"], hist["stop"], hist["time"], hist["avg_r_net"])
        if ci:
            base += " (95%% range %+.2fR to %+.2fR)" % (ci[0], ci[1])
        lines.append(base + ".")
        if n < 15:
            lines.append("That is too few trades to trust either way.")
        elif ci and ci[0] > 0:
            lines.append("Past trades of this rule on this stock made money on average after costs, and the range stays "
                         "above zero. The past is not a promise.")
        elif ci and ci[1] < 0:
            lines.append("Past trades of this rule on this stock lost money on average after costs. The evidence says skip.")
        else:
            lines.append("The range spans zero: no clear edge in the past.")
    if sim:
        lines.append("Simulation: target first %.1f%%, stop first %.1f%%, neither %.1f%%; average %+.2fR after costs." % (
            sim["p_target"] * 100, sim["p_stop"] * 100, sim["p_neither"] * 100, sim["avg_r_net"]))
    good_hist = n >= 15 and (hist.get("ci95_avg_r_net") or [0, 0])[0] > 0
    bad_hist = n >= 15 and (hist.get("ci95_avg_r_net") or [0, 0])[1] < 0
    if bad_hist or (sim and sim["avg_r_net"] < -0.10 and not good_hist):
        overall = "weak"
    elif good_hist and (not sim or sim["avg_r_net"] > 0):
        overall = "supportive"
    else:
        overall = "mixed"
    lines.append({"weak": "Overall evidence: WEAK. Leaning against this trade.",
                  "mixed": "Overall evidence: MIXED. Close to a coin flip after costs.",
                  "supportive": "Overall evidence: SUPPORTIVE in the past. Still a probability, not a promise."}[overall])
    return overall, lines


# ---------------------------------------------------------------- intraday

def slot_of(cal, d, hhmm_end):
    """Slot of the 5-minute bar that ENDS at hh:mm on day d."""
    s = cal.session(d)
    t = datetime.datetime.combine(d, m.parse_hhmm(hhmm_end), m.IST)
    return int((t - s[0]).total_seconds() // 300) - 1


def build_pool(sessions, nslots):
    pool = [[] for _ in range(nslots)]
    for D in sessions:
        for i in range(1, nslots):
            pc = D[i - 1]["c"]
            b = D[i]
            pool[i].append((b["o"] / pc, b["h"] / pc, b["l"] / pc, b["c"] / pc))
    return pool


def simulate(pool, entry, stop, target, k, last, replays, seed, window, cost_at):
    rng = random.Random(seed)
    nslots = len(pool)
    risk = entry - stop
    r_t = (target - entry) / risk
    out = {"target": 0, "stop": 0, "close": 0}
    rs, closes = [], []
    for _ in range(replays):
        p, res = entry, None
        for i in range(k + 1, last + 1):
            j = i if window == 0 else min(nslots - 1, max(1, i + rng.randint(-window, window)))
            o, h, l, c = rng.choice(pool[j])
            if l * p <= stop:
                res = "stop"            # stop and target in one bar: count the loss (conservative)
                break
            if h * p >= target:
                res = "target"
                break
            p = p * c
        res = res or "close"
        out[res] += 1
        exit_px = stop if res == "stop" else target if res == "target" else p
        r = -1.0 if res == "stop" else r_t if res == "target" else (p - entry) / risk
        rs.append(r - cost_at(exit_px))
        if res == "close":
            closes.append(p)
    closes.sort()
    return {"replays": replays, "seed": seed, "window": window, "bars_left": last - k,
            "p_target": round(out["target"] / float(replays), 4), "p_stop": round(out["stop"] / float(replays), 4),
            "p_neither": round(out["close"] / float(replays), 4), "avg_r_net": round(statistics.mean(rs), 3),
            "median_close_if_neither": round(closes[len(closes) // 2], 2) if closes else None}


def history_intraday(sessions, plan, cfg, cal, n_range, late_slot, flat_slot, seed):
    risk_pct = plan["risk_pct"] / 100.0
    r_mult = plan["r_multiple"]
    rule = plan.get("stop_rule", "or-mid")
    sym = plan["symbol"]
    trades = []
    for D in sessions:
        bars = D
        orh = max(b["h"] for b in bars[:n_range])
        orl = min(b["l"] for b in bars[:n_range])
        k = next((i for i in range(n_range, min(late_slot + 1, len(bars))) if bars[i]["c"] > orh), None)
        if k is None:
            continue
        entry = bars[k]["c"]
        if rule == "or-mid":
            stop = (orh + orl) / 2.0
        elif rule == "or-low":
            stop = orl - m.tick_value(orl, sym, cfg)
        else:
            stop = entry * (1 - risk_pct)
        stop = m.floor_tick(stop, sym, cfg)
        if stop >= entry:
            continue
        risk = entry - stop
        target = m.floor_tick(entry + r_mult * risk, sym, cfg)
        res, px = None, None
        for b in bars[k + 1:flat_slot + 1]:
            if b["l"] <= stop:
                res, px = "stop", stop
                break
            if b["h"] >= target:
                res, px = "target", target
                break
        if res is None:
            res, px = "time", bars[min(flat_slot, len(bars) - 1)]["c"]
        qty = ref_qty(cfg, risk, entry)
        r_g = (px - entry) / risk
        trades.append({"date": bars[0]["date"], "entry": round(entry, 2), "exit": round(px, 2), "outcome": res,
                       "r_gross": round(r_g, 3),
                       "r_net": round(r_g - cost_r(entry, px, risk, qty, "intraday", cfg["costs"]), 3)})
    return summarize(trades, seed)


def run_intraday(plan, cfg, cal, src, now, a):
    sym = plan["symbol"]
    ser = src.chart(sym, "60d", "5m")
    bars, _ = m.intraday_bars(ser, cal, now)
    days = m.by_day(bars)
    trade_day = datetime.date.fromisoformat(plan["trade_date"])
    nslots = 75
    sessions = [[days[d][i] for i in range(nslots)] for d in sorted(days)
                if d < trade_day.isoformat() and len(days[d]) == nslots and m.slots_in(cal, datetime.date.fromisoformat(d)) == nslots]
    if len(sessions) < 20:
        raise m.MarketError("only %d full past sessions of 5-minute bars for %s; need 20+" % (len(sessions), sym))
    flat_slot = slot_of(cal, trade_day, plan["flat_by"]) if cal.session(trade_day) else 70
    flat_slot = min(flat_slot, nslots - 1)
    if plan.get("trigger_time"):
        k = slot_of(cal, trade_day, plan["trigger_time"])
    else:
        k = max(0, int((min(now, datetime.datetime.combine(trade_day, m.CLOSE, m.IST)) -
                        datetime.datetime.combine(trade_day, m.OPEN, m.IST)).total_seconds() // 300) - 1)
    if k >= flat_slot:
        raise m.MarketError("the entry bar is at or after the flat-by time; nothing left to simulate")
    entry, stop, target = plan["entry"], plan["stop"], plan["target"]
    risk = entry - stop
    qty = ref_qty(cfg, risk, entry)
    pool = build_pool(sessions, nslots)
    sim = simulate(pool, entry, stop, target, k, flat_slot, a.replays, a.seed, a.window,
                   lambda px: cost_r(entry, px, risk, qty, "intraday", cfg["costs"]))
    sim.update({"sessions": len(sessions), "from": sessions[0][0]["date"], "to": sessions[-1][0]["date"]})
    hist = {"n": 0}
    if plan.get("setup") == "orb":
        n_range = (plan.get("or_minutes") or cfg["intraday"]["or_minutes"]) // 5
        late_slot = slot_of(cal, trade_day, cfg["intraday"]["no_entries_after"]) if cal.session(trade_day) else 62
        hist = history_intraday(sessions, plan, cfg, cal, n_range, late_slot, flat_slot, a.seed)
        hist["rule"] = "first 5-minute close above the %d-minute range by %s, stop %s, target %gR, flat by %s" % (
            n_range * 5, cfg["intraday"]["no_entries_after"], plan.get("stop_rule"), plan["r_multiple"], plan["flat_by"])
    else:
        hist["rule"] = "no history replay for a manual or level entry (no rule to repeat)"
    return sim, hist, qty


# ---------------------------------------------------------------- swing

def walk_swing(days, i0, entry, stop, target, hold, gap_aware=True):
    """Daily bars after entry on day i0 (the entry day is days[i0]). Returns (outcome, exit price, exit index)."""
    for j in range(i0, min(len(days), i0 + hold)):
        b = days[j]
        if j > i0 and gap_aware:
            if b["o"] <= stop:
                return "stop", b["o"], j          # gapped through the stop: filled at the open, worse
            if b["o"] >= target:
                return "target", b["o"], j        # gapped over the target: filled at the open, better
        if b["l"] <= stop:
            return "stop", stop, j
        if b["h"] >= target:
            return "target", target, j
    if i0 + hold > len(days):
        return "open", days[-1]["c"], len(days) - 1     # still running at the end of the data: not scored
    j = i0 + hold - 1
    return "time", days[j]["c"], j


def swing_stop(rule, entry, atr, low10, sym, cfg, risk_pct):
    k = rule.split(":")[0]
    if k == "atr" and atr:
        v = float(rule.split(":")[1]) if ":" in rule else 2.0
        raw = entry - v * atr
    elif k == "low-10d" and low10:
        raw = low10 - m.tick_value(low10, sym, cfg)
    elif k == "pct":
        raw = entry * (1 - float(rule.split(":")[1]) / 100.0)
    else:
        raw = entry * (1 - risk_pct)
    return m.floor_tick(raw, sym, cfg)


def history_swing(days, plan, cfg, seed, max_wait=3):
    sym, rule, hold = plan["symbol"], plan.get("stop_rule", "atr:2"), plan.get("max_hold_days", 10)
    setup = plan.get("setup_name")
    closes = [b["c"] for b in days]
    rsi = m.rsi_series(closes)
    atr = m.atr_series(days)
    trades, i, last_i = [], 21, len(days) - 1
    trade_date = plan["trade_date"]
    while i < last_i:
        if days[i]["date"] >= trade_date:
            break
        if setup not in m.setups_at(closes, i, rsi):
            i += 1
            continue
        trig = m.ceil_tick(days[i]["h"] + m.tick_value(days[i]["h"], sym, cfg), sym, cfg)
        e_i = next((j for j in range(i + 1, min(last_i + 1, i + 1 + max_wait)) if days[j]["h"] >= trig), None)
        if e_i is None:
            i += 1
            continue
        entry = max(days[e_i]["o"], trig)
        low10 = min(b["l"] for b in days[max(0, i - 9):i + 1])
        stop = swing_stop(rule, entry, atr[i], low10, sym, cfg, plan["risk_pct"] / 100.0)
        if stop >= entry:
            i += 1
            continue
        risk = entry - stop
        target = m.floor_tick(entry + plan["r_multiple"] * risk, sym, cfg)
        res, px, x_i = walk_swing(days, e_i, entry, stop, target, hold)
        if res == "open":
            break
        qty = ref_qty(cfg, risk, entry)
        r_g = (px - entry) / risk
        trades.append({"date": days[i]["date"], "entry_date": days[e_i]["date"], "exit_date": days[x_i]["date"],
                       "entry": round(entry, 2), "exit": round(px, 2), "outcome": res, "r_gross": round(r_g, 3),
                       "r_net": round(r_g - cost_r(entry, px, risk, qty, "delivery", cfg["costs"]), 3)})
        i = x_i + 1                         # one trade at a time
    return summarize(trades, seed)


def simulate_swing(days, entry, stop, target, hold, replays, seed, cost_at, lookback=250):
    rel = []
    src = days[-lookback:]
    for j in range(1, len(src)):
        pc = src[j - 1]["c"]
        b = src[j]
        rel.append((b["o"] / pc, b["h"] / pc, b["l"] / pc, b["c"] / pc))
    rng = random.Random(seed)
    risk = entry - stop
    r_t = (target - entry) / risk
    cnt = {"target": 0, "stop": 0, "time": 0}
    rs = []
    for _ in range(replays):
        p, res, px = entry, None, None
        for d in range(hold):
            o, h, l, c = rng.choice(rel)
            if d > 0 and o * p <= stop:
                res, px = "stop", o * p
                break
            if d > 0 and o * p >= target:
                res, px = "target", o * p
                break
            if l * p <= stop:
                res, px = "stop", stop
                break
            if h * p >= target:
                res, px = "target", target
                break
            p = p * c
        if res is None:
            res, px = "time", p
        cnt[res] += 1
        rs.append((px - entry) / risk - cost_at(px))
    return {"replays": replays, "seed": seed, "days": hold, "sample_days": len(rel),
            "p_target": round(cnt["target"] / float(replays), 4), "p_stop": round(cnt["stop"] / float(replays), 4),
            "p_neither": round(cnt["time"] / float(replays), 4), "avg_r_net": round(statistics.mean(rs), 3),
            "r_at_target": round(r_t, 2)}


def run_swing(plan, cfg, cal, src, now, a):
    sym = plan["symbol"]
    days = m.daily_bars(src.chart(sym, a.history, "1d"), cal, now)
    if len(days) < 120:
        raise m.MarketError("only %d daily bars for %s; need 120+" % (len(days), sym))
    entry, stop, target = plan["entry"], plan["stop"], plan["target"]
    risk = entry - stop
    qty = ref_qty(cfg, risk, entry)
    sim = simulate_swing(days, entry, stop, target, plan.get("max_hold_days", 10), a.replays, a.seed,
                         lambda px: cost_r(entry, px, risk, qty, "delivery", cfg["costs"]))
    sim.update({"from": days[-250:][0]["date"], "to": days[-1]["date"]})
    hist = {"n": 0}
    if plan.get("setup_name") in m.SETUP_LABEL:
        hist = history_swing(days, plan, cfg, a.seed)
        hist["rule"] = "%s, entry on a trade above the signal day's high within 3 sessions, stop %s, target %gR, out after %d sessions" % (
            m.SETUP_LABEL[plan["setup_name"]], plan.get("stop_rule"), plan["r_multiple"], plan.get("max_hold_days", 10))
        hist["window"] = "%s to %s" % (days[0]["date"], days[-1]["date"])
    else:
        hist["rule"] = "no history replay: the plan does not name a scanner setup"
    return sim, hist, qty


# ---------------------------------------------------------------- main

def load_plan(a):
    plan = m.read_json(m.tpath("plan.json", a.trader_dir))
    if not plan:
        raise m.MarketError("No trader/plan.json. Run /trader-exit first.")
    if plan["mode"] == "swing" and not plan.get("setup_name"):
        wl = m.read_json(m.tpath("watchlist.json", a.trader_dir), {}) or {}
        hit = next((h for h in wl.get("hits", []) if h["symbol"] == plan["symbol"]), None)
        if a.setup:
            plan["setup_name"] = a.setup
        elif hit and hit.get("setups"):
            plan["setup_name"] = hit["setups"][0]
    elif a.setup:
        plan["setup_name"] = a.setup
    return plan


def run(a):
    cfg = m.load_config(a.trader_dir)
    cal = m.Calendar(cfg)
    now = m.now_ist(a.now)
    plan = load_plan(a)
    src = m.Source(offline=a.offline, pause=0)
    if plan["mode"] == "intraday":
        sim, hist, qty = run_intraday(plan, cfg, cal, src, now, a)
    else:
        sim, hist, qty = run_swing(plan, cfg, cal, src, now, a)
    overall, lines = verdict(sim, hist)
    return {"symbol": plan["symbol"], "mode": plan["mode"], "as_of": now.isoformat(),
            "plan": {k: plan.get(k) for k in ("entry", "stop", "target", "r_multiple", "stop_rule", "flat_by",
                                              "max_hold_days", "setup", "setup_name", "trigger_time")},
            "costs_scored_at_qty": qty, "simulation": sim, "history": hist, "evidence": overall, "verdict": lines}


def render(doc):
    p = doc["plan"]
    out = ["Time machine for %s (%s): entry %s, stop %s, target %s (%gR)." % (
        doc["symbol"], doc["mode"], m.price(p["entry"]), m.price(p["stop"]), m.price(p["target"]), p["r_multiple"])]
    s = doc["simulation"]
    if doc["mode"] == "intraday":
        out.append("Simulation: %s replays of the remaining %d bars to %s, drawn from %d past sessions (%s to %s), seed %d."
                   % ("{:,}".format(s["replays"]), s["bars_left"], p["flat_by"], s["sessions"], s["from"], s["to"], s["seed"]))
    else:
        out.append("Simulation: %s replays of up to %d sessions, drawn from %d daily bars (%s to %s), gaps included, seed %d."
                   % ("{:,}".format(s["replays"]), s["days"], s["sample_days"], s["from"], s["to"], s["seed"]))
    h = doc["history"]
    out.append("History rule: %s." % h.get("rule", "-"))
    if h.get("n"):
        recent = h["trades"][-5:]
        out.append("Last %d: %s" % (len(recent), "; ".join("%s %s %+.2fR" % (t["date"], t["outcome"], t["r_net"]) for t in recent)))
    out.extend(doc["verdict"])
    out.append("Costs are scored at %d shares, roughly what /trader-budget would size. "
               "Next: /trader-budget." % doc["costs_scored_at_qty"])
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="timemachine.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    q = m.add_common(sub.add_parser("run"))
    q.add_argument("--replays", type=int, default=10000)
    q.add_argument("--seed", type=int, default=19)
    q.add_argument("--window", type=int, default=2, help="intraday: draw each bar from +/- this many slots")
    q.add_argument("--history", default="2y", help="swing: how much daily history (Yahoo range: 1y, 2y, 5y)")
    q.add_argument("--setup", choices=sorted(m.SETUP_LABEL), help="swing: which setup to replay")
    q.add_argument("--no-write", action="store_true")
    a = p.parse_args(argv)
    if a.cmd != "run":
        p.print_help()
        return 1
    try:
        doc = run(a)
    except m.MarketError as e:
        return m.fail(e)
    if not a.no_write:
        m.write_json(m.tpath("odds.json", a.trader_dir), doc)
    print(json.dumps(doc, indent=2) if a.json else render(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
