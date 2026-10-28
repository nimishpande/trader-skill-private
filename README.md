# India Trader skills

Claude skills for Indian stocks (NSE). They scan NIFTY 500 for intraday and
swing setups, read the news and BSE filings, time the entry, plan the exit on
NSE ticks, replay the odds after Indian trading charges, size the position,
ring alerts, and keep score of every suggestion.

**Suggestions only.** Nothing here can place an order. Every suggestion comes
with its evidence (simulated odds, how the same rule did on that stock before,
charges, news red flags, your own rules), and you decide.

Free data, no API keys: Yahoo Finance charts for NSE (`SYMBOL.NS`), NSE's index
lists, and BSE's corporate announcements. Plain Python 3.8+, nothing to install.

> Built on the design of Jake Schincariol's
> [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT),
> rewritten for India: IST and the NSE calendar, NSE tick sizes, STT and the
> other Indian charges, BSE filings instead of SEC, and no order placement.

## The skills

| command | what it does |
| --- | --- |
| `/trader-suggest SYMBOL` | **Start here.** Runs the chain and prints one suggestion card with the evidence. |
| `/trader-scanner` | NIFTY 500 (or 50/100/200/all) scans: intraday opening-range breakouts; swing 20-day breakouts, bull flags, 20-DMA reclaims, oversold bounces. |
| `/trader-news` | Move since yesterday, Yahoo headlines, BSE announcements; tags results, orders, pledges, auditor exits, SEBI actions. |
| `/trader-entry` | Has it triggered? Waiting / triggered (fresh or stale) / expired, bar by bar. |
| `/trader-exit` | Stop and target before entry, on valid NSE ticks; flat-by time or max hold. |
| `/trader-timemachine` | 10,000 simulated sessions from the stock's own bars, plus every past time the rule fired, after charges, with a 95% range. |
| `/trader-budget` | Shares that fit your max loss *including charges*, rupees at stop and target, breakeven, rule checks, CONSIDER or SKIP. |
| `/trader-alerts` | Stop / target / flat-by alerts from the plan, price and time alerts, NSE open or closed. |
| `/trader-journal` | Logs every suggestion, taken or not, scores it from the real bars, and reports the real hit rate and calibration. |

## Set it up

### Option A: Claude Code in the cloud (no install)

This repo already holds the skills in `.claude/skills/`, so any Claude Code
cloud session opened on it has the `/trader-...` commands.

1. **Allow the data sites.** At [claude.ai/code](https://claude.ai/code), open
   your cloud environment's settings, set network access to **Custom**, keep
   the default domains included, and add these to **Allowed domains**:
   ```
   query1.finance.yahoo.com
   query2.finance.yahoo.com
   feeds.finance.yahoo.com
   www.nseindia.com
   nsearchives.nseindia.com
   www.bseindia.com
   api.bseindia.com
   ```
   Without this the scripts say "not reachable": cloud sessions only reach
   package registries and GitHub by default.
2. Start a cloud session on this repo and say: *set up my trader config*.
   Claude copies `config.example.json` to `trader/config.json` and asks for
   your capital and the most you will lose on one trade.
3. Say *scan NIFTY 500 for swing setups* or *suggest an intraday trade on
   RELIANCE*.

Cloud sessions start fresh each time. Your `trader/config.json` and
`trader/journal.json` survive only if committed, and Claude offers to commit
them. **If this repo is public, make it private first** (your journal shows your
trades), or delete the two `!trader/...` lines from `.gitignore`.

Two cloud caveats: NSE's website sometimes blocks data-centre servers (if
`scan.py universe` fails, save the CSV in your browser and use `--file`), and
Yahoo may rate-limit them. Running locally avoids both.

### Option B: on your own computer

You need [Claude Code](https://code.claude.com/docs/en/overview) and Python 3.8+.

```bash
git clone https://github.com/Pr-teek/trader-skill.git
cd trader-skill
claude
```

Working inside the repo, the skills load automatically. To have them in every
project, copy the folders: `cp -r .claude/skills/trader-* ~/.claude/skills/`
(Windows PowerShell:
`Copy-Item -Recurse .claude\skills\trader-* "$env:USERPROFILE\.claude\skills\"`).

## A trading day with it

Intraday (MIS, flat by 15:10):

1. **9:00** `/trader-scanner universe` (weekly), `/trader-news` on your names.
2. **9:35 onward** `/trader-scanner orb`: which charts closed above their
   9:15-9:30 range, and when.
3. Pick one. `/trader-suggest SYMBOL` prints the card: entry, stop, target,
   shares, rupees at stop and target after charges, simulated odds, history,
   news flags, checks, evidence label.
4. Your call. If you take it: `/trader-alerts add --from-plan` and
   `/trader-journal take J7 --price YOUR_FILL`. If not: `/trader-journal skip J7`.
5. **After 15:30** `/trader-journal review`. Once a week, `/trader-journal stats`.

Swing (CNC delivery, up to 10 sessions):

1. Before 9:15 or after 15:30: `/trader-scanner swing`.
2. `/trader-suggest SYMBOL --mode swing` for a stock from the list.
3. The entry is a trade at the trigger (one tick above the signal day's high)
   within 3 sessions; the card says what that costs and how it did before.

### What a card looks like

On made-up test data (the `DEMO` stocks are synthetic):

```
SUGGESTION CARD: DEMOBRK, intraday opening-range breakout, Wed 21 Oct 2026 10:06 IST
Setup     the bar ending 10:05 closed at 1006.27, above the opening-range high 1004.27  [fresh]
Plan      entry 1006.27 | stop 999.80 (or-mid) | target 1019.20 (2R) | flat by 15:10
Size      24 shares, ₹24,150 | lose ₹180.84 at the stop | make ₹284.46 at the target (after all charges)
          reward:risk after costs 1.57 (before costs 2.00), breakeven 1007.40
Odds      simulation: target first 10%, stop first 62%, neither 28% (avg -0.46R after costs)
          history: 42 past trades of this rule on DEMOBRK, win 38%, avg -0.38R after costs (95% -0.80 to +0.04)
News      +0.60% vs yesterday's close | ! Resignation of Statutory Auditors: Intimation of resignation of the… | results in the news
Checks    4 pass, 1 warn, 1 skip
          SKIP time machine evidence WEAK (history: 42 trades, -0.38R average after costs): the odds lean against this plan
          WARN news flag: Resignation of Statutory Auditors: Intimation of resignation of the Statutory Auditor of the Company
Evidence  WEAK. Leaning against this trade.
Bottom line: by your own rules and the evidence, this leans SKIP.
```

## How accurate is it?

Accurate about what it can know: correct NSE prices on valid ticks, the same
rule applied the same way every time, every charge counted, and how often this
exact setup worked on this exact stock before. It cannot know the future, and
nothing honest claims to. Intraday breakouts often come out near a coin flip
after charges; the tool says so rather than hiding it.

The real measure is the journal. Log every suggestion, taken or skipped, and
`/trader-journal stats` shows the realized hit rate and average R after costs,
by setup and by evidence label, and compares the time machine's predicted odds
with what happened. After 30 or more suggestions you will know whether the
WEAK / MIXED / SUPPORTIVE labels mean anything for the way you trade.

## Your settings: `trader/config.json`

Start from `config.example.json`. The main ones:

| key | default | meaning |
| --- | --- | --- |
| `capital` | 100000 | rupees you trade with |
| `max_loss_per_trade` | 500 | most one trade may lose at its stop, **charges included** |
| `daily_loss_limit` | 1500 | no new suggestions past this loss in a day |
| `max_trades_per_day` | 3 | |
| `min_net_reward_risk` | 1.5 | reward:risk **after** charges |
| `position_cap_pct` | 25 | one position at most this share of capital |
| `intraday.flat_by` | 15:10 | before your broker's MIS auto square-off |
| `intraday.no_entries_after` | 14:30 | |
| `swing.max_hold_days` | 10 | |
| `costs` | Zerodha | your broker's brokerage and DP charge |
| `tick_overrides` | `{}` | `{"SYMBOL": 0.05}` if your broker shows a different tick |
| `extra_holidays` | `{}` | `{"2027-01-26": "Republic Day"}` until the 2027 list is built in |

**Charges.** The defaults are Zerodha's published rates (read 2026-10-22):
intraday brokerage 0.03% or ₹20 per order, whichever is lower; delivery free;
STT 0.025% on the intraday sell and 0.1% on both sides of delivery; NSE
transaction charge 0.00307%; SEBI ₹10 per crore; GST 18% on brokerage plus
exchange and SEBI charges; stamp duty 0.003% intraday and 0.015% delivery on
the buy; DP ₹15.34 per delivery sell. STT, stamp, SEBI and GST are the same at
every broker; check your broker's charges page and set `costs` for the rest.

## Data and limits

- **Yahoo Finance** is free and unofficial: it can lag, change or rate-limit.
  5-minute bars, not ticks; it keeps only ~60 days of 5-minute history, which
  is why intraday history is short. Check prices with your broker before acting.
- **NSE** lists come from `nsearchives.nseindia.com`; NSE sometimes blocks
  scripts. `scan.py universe --file` takes a CSV you saved yourself.
- **BSE** announcements come from BSE's public API, matched to NSE symbols by
  ISIN. NSE-only stocks have none.
- **Calendar**: NSE's 2026 holidays and the Muhurat session (Sun 8 Nov 2026,
  14:20 to 15:30) are built in. Add later years to `extra_holidays`.
- **Ticks**: NSE's price-band tick table (April 2025); the broker's tick wins.
- **Circuit limits** are not fetched; the checks warn when a stock is within
  1% of a common band (5, 10, 20%).
- **Long only.** No short selling, F&O, options or leverage.
- **Trade-to-trade (BE series)** stocks are excluded from intraday.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation or the future. SEBI's study of FY23
found about 7 in 10 individual intraday traders in the equity cash segment lost
money. Intraday profits are taxed as speculative business income and delivery
gains under capital gains rules; nothing here is tax advice.

**A stop is a plan, not a guarantee.** In a fast market, at a circuit, or across
an overnight gap, a stop can fill well past its price. The loss shown is the loss
at the stop price.

## Development

```bash
python3 -m unittest discover -s tests -v    # offline, synthetic data
python3 tests/make_fixtures.py              # rebuild the synthetic bundle
python3 tools/sync_core.py                  # after editing core/indmkt.py
```

```
core/indmkt.py          shared core (IST, NSE calendar, ticks, charges, data, indicators)
.claude/skills/trader-*  one folder per skill: SKILL.md, script, a copy of indmkt.py
tests/                  unittest suite, fixture generator, synthetic NSE bundle
tools/sync_core.py      copies the core into each skill folder
config.example.json     your settings template
```

MIT licence. See [LICENSE](LICENSE).
