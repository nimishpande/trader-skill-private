# India Trader skills

This repo is a set of Claude skills for Indian stocks (NSE). The skills are in
`.claude/skills/trader-*`; each folder has a SKILL.md and a standard-library
Python script. The user trades Indian stocks only.

- **Suggestions only.** Nothing here places, sends or prepares an order. Never
  tell the user to buy; show the evidence and leave the decision to them.
- **Run scripts from the repo root.** They read and write `trader/` here.
  `trader/config.json` holds the user's capital and limits; if it is missing,
  copy `config.example.json` to `trader/config.json` and ask for capital and
  max loss per trade.
- **Keep the score.** `trader/config.json` and `trader/journal.json` are the
  only files in `trader/` that git keeps. In a cloud session they vanish with
  the session unless committed: after the user changes their config or the
  journal changes (log, take, skip, close, review), offer to commit and push.
- **Network.** The data comes from query1/query2/feeds.finance.yahoo.com,
  nsearchives.nseindia.com, www.nseindia.com, api.bseindia.com and
  www.bseindia.com. If a script says a host is not reachable, the cloud
  environment needs those domains in its Custom network allowlist (README).
- **Times are IST**, NSE hours 9:15 to 15:30. The 2026 NSE holidays are built in;
  later years go in `trader/config.json` under `extra_holidays` until updated.

## Development

- `core/indmkt.py` is the shared core. Edit it there, then run
  `python3 tools/sync_core.py` to copy it into every skill folder.
- Tests: `python3 -m unittest discover -s tests -v` (offline, synthetic data
  from `tests/make_fixtures.py`; all DEMO symbols are made up).
