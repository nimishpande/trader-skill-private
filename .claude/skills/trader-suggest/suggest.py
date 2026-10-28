"""trader-suggest: one stock, one suggestion card, with the evidence. You decide.

  suggest.py run SYMBOL [--mode intraday|swing] [--stop RULE] [--target RULE]
  suggest.py card                 re-print the card from the files already in trader/

`run` chains the other skills in order and stops at the first one that says
there is nothing to do:  news -> entry -> exit -> time machine -> budget -> card.
Intraday runs the opening-range breakout; swing needs the stock in the current
swing watchlist (run the swing scan first). The card is also saved as
trader/suggestion.md. Nothing is ever ordered.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import indmkt as m  # noqa: E402

ROOT = os.path.dirname(HERE)
SCRIPTS = {
    "news": ("trader-news", "news.py"), "entry": ("trader-entry", "entry.py"), "exit": ("trader-exit", "exit.py"),
    "tm": ("trader-timemachine", "timemachine.py"), "budget": ("trader-budget", "size.py"),
}


def script(key):
    folder, name = SCRIPTS[key]
    path = os.path.join(ROOT, folder, name)
    if not os.path.exists(path):
        raise m.MarketError("%s is not installed next to trader-suggest (expected %s). Install all the trader-* folders together."
                            % (folder, path))
    return path


def call(key, args, common):
    cmd = [sys.executable, script(key)] + args + common
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    return r.returncode, r.stdout.strip()


def common_args(a):
    out = []
    if a.now:
        out += ["--now", a.now]
    if a.trader_dir:
        out += ["--trader-dir", a.trader_dir]
    return out


def run_chain(a):
    sym = m.clean_symbol(a.symbol)
    com = common_args(a)
    off = ["--offline", a.offline] if a.offline else []
    steps = []
    rc, out = call("news", ["--tickers", sym, "--all"] + off, com)
    steps.append(("news", out))
    setup = ["--setup", "swing"] if a.mode == "swing" else ["--setup", "orb"]
    rc, out = call("entry", ["check", sym] + setup + off, com)
    steps.append(("entry", out))
    ent = m.read_json(m.tpath("entry.json", a.trader_dir), {}) or {}
    if ent.get("symbol") != sym or ent.get("state") not in ("triggered", "waiting", "not_open"):
        return steps, "stopped at the entry check: %s" % out.splitlines()[-1] if out else "no entry"
    ex = []
    if a.stop:
        ex += ["--stop", a.stop]
    if a.target:
        ex += ["--target", a.target]
    rc, out = call("exit", ["plan"] + ex, com)
    steps.append(("exit", out))
    if rc != 0:
        return steps, "stopped at the exit plan: %s" % out
    rc, out = call("tm", ["run"] + off + (["--replays", str(a.replays)] if a.replays else []), com)
    steps.append(("timemachine", out))
    rc, out = call("budget", [], com)
    steps.append(("budget", out))
    return steps, None


def card(tdir):
    plan = m.read_json(m.tpath("plan.json", tdir))
    if not plan:
        raise m.MarketError("No trader/plan.json yet: run `suggest.py run SYMBOL` first.")
    ent = m.read_json(m.tpath("entry.json", tdir), {}) or {}
    od = m.read_json(m.tpath("odds.json", tdir), {}) or {}
    sz = m.read_json(m.tpath("sizing.json", tdir), {}) or {}
    nw = m.read_json(m.tpath("news.json", tdir), {}) or {}
    sym = plan["symbol"]
    if od.get("symbol") != sym or (od.get("plan") or {}).get("entry") != plan["entry"]:
        od = {}
    if sz.get("symbol") != sym or sz.get("entry") != plan["entry"]:
        sz = {}
    when = datetime.datetime.fromisoformat(plan["as_of"])
    kind = ("intraday opening-range breakout" if plan.get("setup") == "orb" else
            "swing: %s" % m.SETUP_LABEL.get((od.get("plan") or {}).get("setup_name"), "level") if plan["mode"] == "swing" else
            "intraday level")
    L = ["SUGGESTION CARD: %s, %s, %s IST" % (sym, kind, when.strftime("%a %d %b %Y %H:%M"))]
    if plan["mode"] == "intraday" and ent.get("state") == "triggered":
        L.append("Setup     the bar ending %s closed at %s, above the %s %s%s" % (
            ent["trigger_time"], m.price(ent["entry"]), "opening-range high" if ent.get("setup") == "orb" else "level",
            m.price(ent["level"]), "  [STALE: %+.2f%% since]" % ent["since_trigger_pct"] if ent.get("stale") else "  [fresh]"))
    elif ent.get("state") in ("waiting", "not_open"):
        L.append("Setup     not triggered yet: needs %s %s (last %s)" % (
            "a trade at" if plan["mode"] == "swing" else "a 5-minute close above", m.price(ent.get("level")), m.price(ent.get("last"))))
    elif ent.get("state") == "triggered":
        L.append("Setup     triggered at %s, close %s above the trigger %s" % (ent.get("trigger_time"), m.price(ent["entry"]), m.price(ent["level"])))
    tail = "flat by %s" % plan["flat_by"] if plan["mode"] == "intraday" else "out after %d sessions" % plan["max_hold_days"]
    L.append("Plan      entry %s | stop %s (%s) | target %s (%gR) | %s" % (
        m.price(plan["entry"]), m.price(plan["stop"]), plan["stop_rule"], m.price(plan["target"]), plan["r_multiple"], tail))
    if sz.get("qty"):
        L.append("Size      %d shares, %s | lose %s at the stop | make %s at the target (after all charges)" % (
            sz["qty"], m.inr(sz["value"], 0), m.inr(sz["loss_at_stop"]), m.inr(sz["gain_at_target"])))
        L.append("          reward:risk after costs %.2f (before costs %.2f), breakeven %s" % (
            sz["net_rr"], plan["r_multiple"], m.price(sz["breakeven"])))
    elif sz:
        L.append("Size      none fits: %s" % sz["checks"][0]["text"])
    if od:
        s, h = od.get("simulation") or {}, od.get("history") or {}
        L.append("Odds      simulation: target first %.0f%%, stop first %.0f%%, neither %.0f%% (avg %+.2fR after costs)" % (
            s["p_target"] * 100, s["p_stop"] * 100, s["p_neither"] * 100, s["avg_r_net"]))
        if h.get("n"):
            c = h.get("ci95_avg_r_net") or [0, 0]
            L.append("          history: %d past trades of this rule on %s, win %.0f%%, avg %+.2fR after costs (95%% %+.2f to %+.2f)" % (
                h["n"], sym, h["win_rate"] * 100, h["avg_r_net"], c[0], c[1]))
        else:
            L.append("          history: no past trades of this rule to learn from")
    else:
        L.append("Odds      not run for this plan (/trader-timemachine)")
    st = next((x for x in nw.get("stocks", []) if x["symbol"] == sym), None)
    if st:
        flags = [i for i in st["items"] if i.get("flag")]
        res = [i for i in st["items"] if "results" in i["tags"] or "results_due" in i["tags"]]
        bits = ["! " + m.clip(i["title"], 70) for i in flags[:2]]
        if res:
            bits.append("results in the news")
        if st.get("move"):
            bits.insert(0, "%+.2f%% %s" % (st["move"]["pct"], st["move"]["label"]))
        L.append("News      %s" % (" | ".join(bits) if bits else "nothing notable in the window"))
    if sz.get("checks"):
        n = {k: sum(1 for c in sz["checks"] if c["level"] == k) for k in ("PASS", "WARN", "SKIP")}
        L.append("Checks    %d pass, %d warn, %d skip" % (n["PASS"], n["WARN"], n["SKIP"]))
        for c in sz["checks"]:
            if c["level"] != "PASS":
                L.append("          %s %s" % (c["level"], c["text"]))
    if od:
        L.append("Evidence  %s" % od["verdict"][-1].replace("Overall evidence: ", ""))
    sug = sz.get("suggestion")
    if sug == "skip":
        L.append("Bottom line: by your own rules and the evidence, this leans SKIP.")
    elif sug:
        L.append("Bottom line: nothing in your rules rules it out (%s). The decision is yours." % sug)
    L.append("Not financial advice. Log it to keep score: /trader-journal log.")
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(prog="suggest.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    r = m.add_common(sub.add_parser("run"))
    r.add_argument("symbol")
    r.add_argument("--mode", choices=("intraday", "swing"), default="intraday")
    r.add_argument("--stop")
    r.add_argument("--target")
    r.add_argument("--replays", type=int)
    r.add_argument("--steps", action="store_true", help="also print each skill's own output")
    m.add_common(sub.add_parser("card"))
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 1
    try:
        if a.cmd == "run":
            steps, stopped = run_chain(a)
            if a.steps or stopped:
                for name, out in steps:
                    print("---- %s\n%s" % (name, out))
            if stopped:
                print("No suggestion: %s" % stopped)
                return 1
        text = card(a.trader_dir)
    except m.MarketError as e:
        return m.fail(e)
    with open(m.tpath("suggestion.md", a.trader_dir), "w", encoding="utf-8") as fh:
        fh.write("```\n" + text + "\n```\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
