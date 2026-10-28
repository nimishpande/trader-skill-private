---
name: trader-alerts
description: >-
  Price and time alerts for Indian stocks, plus NSE market status. Turns a
  trade plan into its stop, target and flat-by (or max-hold) alerts in one go,
  sets above / below price levels and time-of-day reminders, rings each alert
  once, and knows NSE hours (9:15 to 15:30 IST), NSE holidays and Muhurat
  trading. Price alerts check every 5-minute bar since they were set, so a
  quick spike is not missed. Use when the user says "alert me when", "tell me
  if INFY hits", "watch my stop", "remind me to square off", "is the market
  open", "when does NSE open", "is it a holiday", or right after /trader-exit.
argument-hint: "status | add ... | check | list"
---

# trader-alerts

`${CLAUDE_SKILL_DIR}` is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" status                        # open / pre-open / closed, next session
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat-by from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add RELIANCE --above 2950 --note "range high"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add RELIANCE --below 2880
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add RELIANCE --time 15:10 --note "square off"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --open-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # rings what was hit, once
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" remove 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clear                          # drop rung alerts
```

Writes trader/alerts.json.

## How it rings

These alerts ring only when `check` runs. To have them checked on a schedule,
set up a scheduled task (for example every 15 minutes, 9:15 to 15:30 IST on
weekdays) that runs `alerts.py check` and tells the user what rang. The broker's
own price alerts are faster: suggest setting the stop there too.

## Rules for Claude

- When an alert rings, quote it exactly, with the bar time and price.
- A stop alert ringing is information, not an instruction: the user decides.
- Data is free Yahoo 5-minute bars, minutes old.

## Safety

Suggestions only, not financial advice. Nothing is ever placed.
