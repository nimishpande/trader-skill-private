"""trader-exit: where the stop and the target go, decided before any entry.

  exit.py plan [--stop RULE] [--target RULE] [--entry PRICE --symbol SYM --mode intraday|swing]

Reads trader/entry.json (from trader-entry) unless you give --entry/--symbol.
Stop rules:   or-mid (intraday default), or-low, atr:2 (swing default, daily ATR14),
              low-10d (just under the 10-day low), pct:1.5, price:1234.50
Target rules: 2R (default), 1.5R, pct:3, price:1260
Prices are rounded to a valid NSE tick: the stop down (away from the entry),
the target down (a little easier to reach, never flattering the reward).
Intraday plans also carry the flat-by time; swing plans a maximum holding period.
Writes trader/plan.json. It plans; it never places an order.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indmkt as m  # noqa: E402


def parse_rule(text, kinds, what):
    t = (text or "").strip().lower()
    if t.endswith("r") and t[:-1].replace(".", "", 1).isdigit():
        if "R" not in kinds:
            raise m.MarketError("%s %r: an R multiple only makes sense for the target" % (what, text))
        return ("R", float(t[:-1]))
    if ":" in t:
        k, v = t.split(":", 1)
        if k in kinds:
            try:
                return (k, float(v))
            except ValueError:
                pass
    elif t in kinds:
        return (t, None)
    raise m.MarketError("%s %r is not one of: %s" % (what, text, ", ".join(sorted(kinds))))


STOP_KINDS = {"or-mid", "or-low", "atr", "low-10d", "pct", "price"}
TARGET_KINDS = {"R", "pct", "price"}


def compute(entry, stop_rule, target_rule, sym, cfg, or_high=None, or_low=None, atr14=None, low_10d=None):
    k, v = stop_rule
    if k == "or-mid":
        if or_high is None or or_low is None:
            raise m.MarketError("or-mid needs the opening range (run /trader-entry with --setup orb first)")
        raw, why = (or_high + or_low) / 2.0, "opening-range midpoint"
    elif k == "or-low":
        if or_low is None:
            raise m.MarketError("or-low needs the opening range")
        raw, why = or_low - m.tick_value(or_low, sym, cfg), "just under the opening-range low"
    elif k == "atr":
        if not atr14:
            raise m.MarketError("atr needs the daily ATR (run /trader-entry --setup swing, or pick another stop)")
        raw, why = entry - v * atr14, "%g x daily ATR14 (%s) below the entry" % (v, m.price(atr14))
    elif k == "low-10d":
        if low_10d is None:
            raise m.MarketError("low-10d needs the 10-day low (run /trader-entry --setup swing)")
        raw, why = low_10d - m.tick_value(low_10d, sym, cfg), "just under the 10-day low %s" % m.price(low_10d)
    elif k == "pct":
        raw, why = entry * (1 - v / 100.0), "%g%% below the entry" % v
    else:
        raw, why = v, "your price"
    stop = m.floor_tick(raw, sym, cfg)
    if stop >= entry:
        raise m.MarketError("the stop %s is at or above the entry %s: no long plan possible with this rule"
                            % (m.price(stop), m.price(entry)))
    risk = entry - stop
    tk = m.tick_value(entry, sym, cfg)
    if risk < 3 * tk or risk / entry < 0.0015:
        raise m.MarketError("the stop %s is only %s below the entry (%.2f%%): inside the normal bar-to-bar noise. "
                            "Pick a wider rule (or-low, atr:1.5, pct:1)." % (m.price(stop), m.price(risk), risk / entry * 100))
    k2, v2 = target_rule
    if k2 == "R":
        raw_t, why_t = entry + v2 * risk, "%gR (%g x the risk)" % (v2, v2)
    elif k2 == "pct":
        raw_t, why_t = entry * (1 + v2 / 100.0), "%g%% above the entry" % v2
    else:
        raw_t, why_t = v2, "your price"
    target = m.floor_tick(raw_t, sym, cfg)
    if target <= entry:
        raise m.MarketError("the target %s is at or below the entry %s" % (m.price(target), m.price(entry)))
    return {"entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2),
            "risk_per_share": round(risk, 2), "reward_per_share": round(target - entry, 2),
            "r_multiple": round((target - entry) / risk, 2), "stop_why": why, "target_why": why_t,
            "tick": tk, "stop_tick": m.tick_value(stop, sym, cfg), "risk_pct": round(risk / entry * 100, 2)}


def build(a):
    cfg = m.load_config(a.trader_dir)
    now = m.now_ist(a.now)
    ent = {} if a.entry else (m.read_json(m.tpath("entry.json", a.trader_dir), {}) or {})
    if not a.entry and not ent:
        raise m.MarketError("No trader/entry.json. Run /trader-entry first, or pass --entry PRICE --symbol SYM.")
    if not a.entry and ent.get("state") not in ("triggered", "waiting", "not_open"):
        raise m.MarketError("The last entry check for %s says %s; nothing to plan. Re-run /trader-entry."
                            % (ent.get("symbol"), (ent.get("state") or "?").replace("_", " ")))
    sym = m.clean_symbol(a.symbol or ent.get("symbol") or "")
    if not sym:
        raise m.MarketError("Which stock? Pass --symbol.")
    mode = a.mode or ent.get("mode") or "intraday"
    entry = a.entry or ent.get("entry") or ent.get("level")
    if ent.get("state") in ("waiting", "not_open") and not a.entry:
        if ent.get("mode") == "swing":          # the swing trigger is already the first tick above the high
            entry = ent["level"]
        else:
            entry = m.ceil_tick(ent["level"] + m.tick_value(ent["level"], sym, cfg), sym, cfg)
    if not entry:
        raise m.MarketError("No entry price. Pass --entry PRICE.")
    sec = cfg["intraday"] if mode == "intraday" else cfg["swing"]
    stop_rule = parse_rule(a.stop or sec["stop"], STOP_KINDS, "--stop")
    target_rule = parse_rule(a.target or sec["target"], TARGET_KINDS, "--target")
    plan = compute(float(entry), stop_rule, target_rule, sym, cfg, ent.get("or_high"), ent.get("or_low"),
                   a.atr or ent.get("atr14"), ent.get("low_10d"))
    plan.update({"symbol": sym, "mode": mode, "product": "intraday" if mode == "intraday" else "delivery",
                 "as_of": now.isoformat(), "stop_rule": a.stop or sec["stop"], "target_rule": a.target or sec["target"],
                 "entry_state": ent.get("state", "manual"), "trigger_time": ent.get("trigger_time"),
                 "or_high": ent.get("or_high"), "or_low": ent.get("or_low"), "or_minutes": ent.get("or_minutes"),
                 "atr14": a.atr or ent.get("atr14"), "setup": ent.get("setup", "manual"),
                 "trade_date": now.date().isoformat()})
    if mode == "intraday":
        plan["flat_by"] = a.flat_by or cfg["intraday"]["flat_by"]
        m.parse_hhmm(plan["flat_by"], "--flat-by")
    else:
        plan["max_hold_days"] = a.max_hold_days or cfg["swing"]["max_hold_days"]
    warn = []
    if mode == "intraday" and plan["risk_pct"] > 3:
        warn.append("the stop is %.1f%% away: wide for a day trade" % plan["risk_pct"])
    if mode == "swing" and plan["risk_pct"] > 12:
        warn.append("the stop is %.1f%% away: a big gap for one position" % plan["risk_pct"])
    if ent.get("stale"):
        warn.append("the trigger is stale: entering now costs more than %s, and the real R is smaller" % m.price(entry))
    if ent.get("state") in ("waiting", "not_open"):
        warn.append("not triggered yet: this plans the entry at the first tick above the trigger, %s" % m.price(entry))
    plan["warnings"] = warn
    return plan


def render(p):
    head = "Plan for %s (%s), entry %s%s:" % (p["symbol"], p["mode"], m.price(p["entry"]),
                                            " (trigger at %s)" % p["trigger_time"] if p.get("trigger_time") else "")
    out = [head,
           "  stop   %10s  %s, rounded to the %s tick. Risk %s a share (%.2f%%)." % (
               m.price(p["stop"]), p["stop_why"], m.price(p["stop_tick"]), m.price(p["risk_per_share"]), p["risk_pct"]),
           "  target %10s  %s. Reward %s a share." % (m.price(p["target"]), p["target_why"], m.price(p["reward_per_share"]))]
    if p["mode"] == "intraday":
        out.append("  flat by %s IST if neither is hit (before your broker's auto square-off)." % p["flat_by"])
    else:
        out.append("  out after %d sessions if neither is hit. Delivery (CNC): costs and gap risk are higher." % p["max_hold_days"])
    for w in p["warnings"]:
        out.append("  note: " + w)
    out.append("These are before costs. /trader-budget sizes it and shows reward:risk after costs. "
               "Next: /trader-timemachine for the odds.")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="exit.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    q = m.add_common(sub.add_parser("plan"))
    q.add_argument("--stop")
    q.add_argument("--target")
    q.add_argument("--entry", type=float)
    q.add_argument("--symbol")
    q.add_argument("--mode", choices=("intraday", "swing"))
    q.add_argument("--atr", type=float, help="daily ATR14 if not in entry.json")
    q.add_argument("--flat-by")
    q.add_argument("--max-hold-days", type=int)
    q.add_argument("--no-write", action="store_true")
    a = p.parse_args(argv)
    if a.cmd != "plan":
        p.print_help()
        return 1
    try:
        plan = build(a)
    except m.MarketError as e:
        return m.fail("No plan: %s" % e)
    if not a.no_write:
        m.write_json(m.tpath("plan.json", a.trader_dir), plan)
    print(json.dumps(plan, indent=2) if a.json else render(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
