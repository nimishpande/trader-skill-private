---
name: trader-suggest
description: >-
  The front door of the India Trader skills. Gives ONE suggestion card for an
  NSE stock with the evidence behind it: the setup, entry, stop, target, share
  count, rupees lost at the stop and made at the target after every Indian
  charge (STT, brokerage, exchange, GST, stamp, DP), the simulated odds, how the
  same rule did on that stock in the past (with a 95% range), news red flags,
  rule checks, and an evidence label. Never places orders; the user decides.
  Use when the user says "should I trade RELIANCE", "give me a suggestion",
  "what do you think of TATAMOTORS today", "suggest a trade", "is this a good
  setup", "run the whole thing on INFY", "swing trade idea", or after picking a
  stock from /trader-scanner.
argument-hint: "SYMBOL [--mode intraday|swing]"
---

# trader-suggest

This skill turns one NSE stock into one suggestion card. It runs the other
trader skills in order (news, entry, exit, time machine, budget) and stops at
the first one that says there is nothing to do. It is a decision aid. It never
places, sends or prepares an order, and the user always makes the call.

`${CLAUDE_SKILL_DIR}` is this skill's folder. The other `trader-*` folders must
sit next to it (they do when the repo or the whole set is installed).

```bash
python3 "${CLAUDE_SKILL_DIR}/suggest.py" run RELIANCE                  # intraday opening-range breakout
python3 "${CLAUDE_SKILL_DIR}/suggest.py" run TATAMOTORS --mode swing   # needs it in the swing watchlist
python3 "${CLAUDE_SKILL_DIR}/suggest.py" run INFY --stop or-low --target 1.5R
python3 "${CLAUDE_SKILL_DIR}/suggest.py" card                          # re-print the last card
```

Add `--steps` to see each skill's own output. The card is saved to
`trader/suggestion.md`.

## Steps

1. If the user has no `trader/config.json`, copy `config.example.json` from the
   repo root to `trader/config.json` and ask for their capital and the most they
   will lose on one trade (in rupees). Those two numbers drive the share count.
2. For swing, make sure the stock is in the current swing watchlist
   (`/trader-scanner` swing). For intraday, the market must be open and past
   9:35 IST.
3. Run `suggest.py run SYMBOL`. Show the card as it is, in a code block.
4. Then say, in two or three plain sentences, what the evidence says and what
   would change the picture. Lead with the evidence label (WEAK / MIXED /
   SUPPORTIVE) and the after-cost numbers, not the setup.
5. Offer `/trader-journal log` so the suggestion gets scored later, and
   `/trader-alerts add --from-plan` if they take it.

## How to talk about it

- Never say "buy", "a good trade", "a winner" or "I recommend". Say what the
  evidence shows: "the rule has lost money on this stock after costs", "close
  to a coin flip", "the past supports it, which is not a promise".
- When the card says the evidence is weak or the rules say skip, say so plainly
  and do not soften it. When it is supportive, say it is still a probability.
- Always mention costs when they change the picture (a 2R target that is 1.3R
  after charges is not the same trade).
- If the trigger is stale, say the real entry would be worse than the plan.
- Quote the history sample size. Under 15 trades is "too few to trust".

## Safety

Suggestions only, not financial advice. Nothing in this pack can place an
order. SEBI's study found about 7 in 10 individual intraday traders in the
equity cash segment lost money in FY23. Data is free Yahoo Finance bars that can
lag; check prices with the broker before acting.
