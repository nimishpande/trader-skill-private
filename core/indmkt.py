"""indmkt: the shared India-market core of the India Trader skills.

Every skill folder carries an identical copy of this file so each one works on
its own (copy one folder, it still runs). The source of truth is core/indmkt.py;
tools/sync_core.py copies it out and tests/test_core.py checks the copies match.

What lives here:
  - the clock: India Standard Time (UTC+5:30, no daylight saving)
  - the NSE calendar: 9:15 to 15:30, weekends, exchange holidays, special
    sessions (Muhurat trading), plus anything you add in trader/config.json
  - NSE tick sizes and rounding to a valid price
  - Indian trading costs: brokerage, STT, exchange charges, SEBI fee, GST,
    stamp duty, DP charge (defaults: Zerodha's published rates)
  - free data: Yahoo Finance charts for NSE (SYMBOL.NS), NSE index lists,
    BSE corporate announcements, with an offline mode for tests and replays
  - bar parsing, indicators (SMA, RSI, ATR) and the daily setups, shared by the
    scanner and the time machine so a backtest tests exactly the rule you scan

Standard library only. Python 3.8 or newer.
"""

import datetime
import gzip
import http.cookiejar
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CORE_VERSION = "1.0.0"

# ---------------------------------------------------------------- the clock

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
PRE_OPEN = datetime.time(9, 0)


class MarketError(Exception):
    """A plain-language problem the user should read (one line)."""


def now_ist(text=None):
    """--now "2026-10-21 10:15" (IST) or the real clock."""
    if not text:
        return datetime.datetime.now(IST)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text.strip(), fmt).replace(tzinfo=IST)
        except ValueError:
            pass
    raise MarketError('--now wants "YYYY-MM-DD HH:MM" in India time, got %r' % text)


def from_ts(ts):
    return datetime.datetime.fromtimestamp(int(ts), IST)


def parse_hhmm(text, what="time"):
    try:
        h, m = [int(x) for x in str(text).strip().split(":")]
        return datetime.time(h, m)
    except (ValueError, TypeError):
        raise MarketError("%s wants HH:MM (24-hour, India time), got %r" % (what, text))


def clock(t):
    """10:05 -> '10:05', 14:30 -> '14:30' (24-hour IST: no AM/PM mix-ups)."""
    if isinstance(t, datetime.datetime):
        t = t.time()
    return "%02d:%02d" % (t.hour, t.minute)


def day_label(d):
    return d.strftime("%a %d %b %Y")


def minutes_of(t):
    return t.hour * 60 + t.minute


# ---------------------------------------------------------------- the NSE calendar

# NSE equity-segment trading holidays (weekday ones; weekend ones need no entry).
# Source: NSE's 2026 holiday circular as listed by cleartax.in/s/nse-holidays-2026,
# read 2026-10-22. NSE publishes the next year's list in December: add it to
# trader/config.json "extra_holidays" until this file is updated.
HOLIDAYS = {
    "2026-01-15": "Maharashtra municipal elections",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)

# Special sessions: date -> (open, close, name). Muhurat trading 2026 (Sunday
# Nov 8): normal market 14:20 to 15:30 IST (businesstoday.in, 2026-10-19).
SPECIAL_SESSIONS = {
    "2026-11-08": ("14:20", "15:30", "Muhurat trading (Diwali)"),
}


class Calendar(object):
    """NSE sessions. extra_holidays / special_sessions from config add to the built-ins."""

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.holidays = dict(HOLIDAYS)
        self.holidays.update(cfg.get("extra_holidays") or {})
        self.special = dict(SPECIAL_SESSIONS)
        for k, v in (cfg.get("special_sessions") or {}).items():
            self.special[k] = (v[0], v[1], v[2] if len(v) > 2 else "special session")
        years = set(CALENDAR_YEARS)
        years.update(int(k[:4]) for k in (cfg.get("extra_holidays") or {}))
        years.update(int(y) for y in (cfg.get("calendar_years") or []))
        self.years = years

    def known(self, d):
        return d.year in self.years

    def session(self, d):
        """(open_dt, close_dt, note) for date d, or None when the market is shut."""
        key = d.isoformat()
        if key in self.special:
            o, c, name = self.special[key]
            return (datetime.datetime.combine(d, parse_hhmm(o), IST),
                    datetime.datetime.combine(d, parse_hhmm(c), IST), name)
        if d.weekday() >= 5 or key in self.holidays:
            return None
        return (datetime.datetime.combine(d, OPEN, IST), datetime.datetime.combine(d, CLOSE, IST), "")

    def closed_reason(self, d):
        key = d.isoformat()
        if key in self.holidays:
            return "an NSE holiday (%s)" % self.holidays[key]
        if d.weekday() >= 5:
            return "a weekend"
        return ""

    def next_session(self, after):
        """The first session whose close is after `after` (a datetime)."""
        d = after.date()
        for _ in range(30):
            s = self.session(d)
            if s and s[1] > after:
                return s
            d += datetime.timedelta(days=1)
        raise MarketError("no NSE session in the next 30 days (calendar needs updating?)")

    def prev_session_date(self, d):
        d = d - datetime.timedelta(days=1)
        for _ in range(30):
            if self.session(d):
                return d
            d -= datetime.timedelta(days=1)
        return None

    def status(self, now):
        """('open'|'pre-open'|'closed', plain sentence)."""
        s = self.session(now.date())
        warn = "" if self.known(now.date()) else (
            " (no NSE holiday list loaded for %d: add it to trader/config.json extra_holidays)" % now.year)
        if s:
            o, c, note = s
            pre = o.replace(hour=PRE_OPEN.hour, minute=PRE_OPEN.minute) if not note else o - datetime.timedelta(minutes=15)
            if o <= now < c:
                return "open", "NSE is open%s until %s IST%s." % (" for " + note if note else "", clock(c), warn)
            if pre <= now < o:
                return "pre-open", "NSE pre-open; the market opens at %s IST%s." % (clock(o), warn)
        nxt = self.next_session(now)
        if not warn and not self.known(nxt[0].date()):
            warn = " (no NSE holiday list loaded for %d: add it to trader/config.json extra_holidays)" % nxt[0].year
        why = self.closed_reason(now.date())
        return "closed", "NSE is closed%s. Next session: %s, %s to %s IST%s%s." % (
            " today, " + why if why and not s else "", day_label(nxt[0].date()), clock(nxt[0]), clock(nxt[1]),
            " (" + nxt[2] + ")" if nxt[2] else "", warn)


# ---------------------------------------------------------------- tick sizes

# NSE cash-segment tick sizes by price band, revised April 15 2025 (angelone.in
# summary of the NSE circular). NSE fixes each stock's tick from a reference
# price and revises it periodically, so your broker's tick is the final word:
# override per symbol with "tick_overrides": {"SYMBOL": 0.05} in config.
TICK_BANDS = [  # (price at or below, tick in paise)
    (250.0, 1),
    (1000.0, 5),
    (5000.0, 10),
    (10000.0, 50),
    (20000.0, 100),
    (float("inf"), 500),
]


def tick_paise(price, symbol=None, cfg=None):
    over = ((cfg or {}).get("tick_overrides") or {}).get((symbol or "").upper())
    if over:
        return int(round(float(over) * 100))
    for bound, t in TICK_BANDS:
        if price <= bound:
            return t
    return 500


def _paise(x):
    return round(float(x) * 100, 6)


def floor_tick(price, symbol=None, cfg=None):
    t = tick_paise(price, symbol, cfg)
    return math.floor(_paise(price) / t) * t / 100.0


def ceil_tick(price, symbol=None, cfg=None):
    t = tick_paise(price, symbol, cfg)
    return math.ceil(_paise(price) / t) * t / 100.0


def tick_value(price, symbol=None, cfg=None):
    return tick_paise(price, symbol, cfg) / 100.0


# ---------------------------------------------------------------- trading costs

# Defaults: Zerodha's published equity charges (zerodha.com/charges, read
# 2026-10-22). Every broker differs on brokerage and DP charges: put yours in
# trader/config.json "costs". Statutory charges (STT, stamp, GST, SEBI) are the
# same everywhere; exchange charges differ slightly between NSE and BSE.
DEFAULT_COSTS = {
    "source": "zerodha.com/charges, read 2026-10-22",
    "brokerage_intraday_pct": 0.03,     # per executed order, capped below
    "brokerage_intraday_cap": 20.0,
    "brokerage_delivery_pct": 0.0,      # Zerodha: free delivery
    "brokerage_delivery_cap": 0.0,
    "stt_intraday_sell_pct": 0.025,
    "stt_delivery_pct": 0.1,            # buy and sell
    "exchange_txn_pct": 0.00307,        # NSE; BSE is 0.00375
    "sebi_per_crore": 10.0,
    "gst_pct": 18.0,                    # on brokerage + exchange + SEBI
    "stamp_intraday_buy_pct": 0.003,
    "stamp_delivery_buy_pct": 0.015,
    "dp_charge_per_sell": 15.34,        # delivery sells only, per scrip per day
}


def _brokerage(value, pct, cap):
    b = value * pct / 100.0
    return min(cap, b) if cap and cap > 0 else b


def trade_costs(qty, buy_price, sell_price, product="intraday", costs=None):
    """Every charge on one round trip (buy then sell), in rupees.
    product: "intraday" (MIS, same day) or "delivery" (CNC, swing / investing)."""
    c = dict(DEFAULT_COSTS)
    c.update(costs or {})
    buy_v, sell_v = qty * buy_price, qty * sell_price
    turnover = buy_v + sell_v
    if product == "intraday":
        brokerage = (_brokerage(buy_v, c["brokerage_intraday_pct"], c["brokerage_intraday_cap"]) +
                     _brokerage(sell_v, c["brokerage_intraday_pct"], c["brokerage_intraday_cap"]))
        stt = sell_v * c["stt_intraday_sell_pct"] / 100.0
        stamp = buy_v * c["stamp_intraday_buy_pct"] / 100.0
        dp = 0.0
    elif product == "delivery":
        brokerage = (_brokerage(buy_v, c["brokerage_delivery_pct"], c["brokerage_delivery_cap"]) +
                     _brokerage(sell_v, c["brokerage_delivery_pct"], c["brokerage_delivery_cap"]))
        stt = turnover * c["stt_delivery_pct"] / 100.0
        stamp = buy_v * c["stamp_delivery_buy_pct"] / 100.0
        dp = c["dp_charge_per_sell"] if qty > 0 else 0.0
    else:
        raise MarketError("product must be intraday or delivery, got %r" % product)
    exch = turnover * c["exchange_txn_pct"] / 100.0
    sebi = turnover * c["sebi_per_crore"] / 1e7
    gst = (brokerage + exch + sebi) * c["gst_pct"] / 100.0
    parts = {"brokerage": brokerage, "stt": stt, "exchange": exch, "sebi": sebi,
             "gst": gst, "stamp": stamp, "dp": dp}
    parts = {k: round(v, 2) for k, v in parts.items()}
    parts["total"] = round(sum(v for k, v in parts.items()), 2)
    return parts


# ---------------------------------------------------------------- money formatting

def inr(x, decimals=2):
    """1234567.5 -> '₹12,34,567.50' (Indian digit grouping)."""
    if x is None:
        return "-"
    neg = x < 0
    s = ("%." + str(decimals) + "f") % abs(x)
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + "₹" + whole + ("." + frac if frac else "")


def price(x):
    return "-" if x is None else "%.2f" % x


def clip(text, n):
    """Cut at a word boundary with an ellipsis."""
    text = (text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + "\u2026"


def pct(x, signed=True):
    return "-" if x is None else (("%+.2f%%" if signed else "%.2f%%") % x)


# ---------------------------------------------------------------- config and files

DEFAULT_CONFIG = {
    "capital": 100000.0,               # rupees you trade with
    "max_loss_per_trade": 500.0,       # rupees lost if the stop fills, costs included
    "daily_loss_limit": 1500.0,        # stop suggesting new trades past this
    "max_trades_per_day": 3,
    "min_net_reward_risk": 1.5,        # reward:risk AFTER costs
    "position_cap_pct": 25.0,          # one position at most this share of capital
    "cash_buffer_pct": 10.0,           # never plan to use the last 10%
    "intraday_leverage": 1.0,          # 1 = your own money only (MIS margin is a broker choice)
    "min_price": 20.0,
    "universe": "nifty500",
    "intraday": {
        "or_minutes": 15,              # opening range: 9:15 to 9:30
        "no_entries_after": "14:30",
        "flat_by": "15:10",            # before brokers' auto square-off (~15:15 to 15:25)
        "stop": "or-mid",
        "target": "2R",
    },
    "swing": {
        "stop": "atr:2",
        "target": "2R",
        "max_hold_days": 10,
        "min_turnover_cr": 2.0,        # last session price x volume, in crore
    },
    "costs": {},
    "tick_overrides": {},
    "extra_holidays": {},
    "special_sessions": {},
}


def trader_dir(override=None):
    return override or os.environ.get("TRADER_DIR") or os.path.join(os.getcwd(), "trader")


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(tdir=None):
    path = os.path.join(trader_dir(tdir), "config.json")
    user = read_json(path, {})
    if not isinstance(user, dict):
        raise MarketError("trader/config.json is not a JSON object")
    return _merge(DEFAULT_CONFIG, user)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except ValueError:
        raise MarketError("%s is not valid JSON" % path)


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def show_path(p):
    rel = os.path.relpath(p)
    return p if rel.startswith("..") else rel


def tpath(name, tdir=None):
    return os.path.join(trader_dir(tdir), name)


# ---------------------------------------------------------------- symbols

def clean_symbol(sym):
    s = (sym or "").strip().upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            return s[:-3]
    return s


def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS; an explicit .BO (BSE) is kept."""
    s = (sym or "").strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def split_symbols(text):
    return [clean_symbol(x) for x in (text or "").replace(";", ",").split(",") if x.strip()]


# ---------------------------------------------------------------- fetching

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
NSE_HOME = "https://www.nseindia.com/"
NSE_INDEX_CSV = {
    "nifty50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "nifty100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "nifty200": "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "nifty500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "all": "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
}
BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"
BSE_HOME = "https://www.bseindia.com/"
BSE_ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"

SLEEP = time.sleep  # tests replace this


def blocked_hint(host):
    return ("%s is not reachable from here. In a Claude cloud session, set the environment's network "
            "access to Custom and add %s to Allowed domains (see the README), or run on your own computer."
            % (host, host))


class Source(object):
    """Where data comes from: the internet (polite, retrying) or an offline bundle.

    An offline bundle is a .json or .json.gz file (or a folder of them) holding
    {"chart:SYM:5m": <Yahoo v8 chart JSON>, "chart:SYM:1d": ..., "rss:SYM": "<xml>",
     "nse:nifty500": "<csv>", "bse_lookup:SYM": "<text>", "bse_ann:CODE": <json>}.
    """

    def __init__(self, offline=None, pause=0.3, quiet=False):
        self.offline = load_bundle(offline) if offline else None
        self.pause = pause
        self.quiet = quiet
        self.requests = 0
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._nse_primed = False
        self._bse_primed = False

    # -- raw HTTP
    def _get(self, url, headers=None, timeout=20):
        if self.offline is not None:
            raise MarketError("offline mode: nothing saved for %s" % url.split("?")[0])
        h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-IN,en;q=0.9"}
        h.update(headers or {})
        req = urllib.request.Request(url, headers=h)
        self.requests += 1
        with self._opener.open(req, timeout=timeout) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data.decode("utf-8", "replace")

    def _host(self, url):
        return urllib.parse.urlparse(url).netloc

    def get_text(self, url, headers=None, retries=1):
        last = None
        for attempt in range(retries + 1):
            try:
                return self._get(url, headers)
            except urllib.error.HTTPError as e:
                last = e
                code = e.code
                e.close()
                if code == 429 or code >= 500:
                    SLEEP(5 if code == 429 else 2)
                    continue
                raise MarketError("%s answered HTTP %d" % (self._host(url), code))
            except (urllib.error.URLError, OSError) as e:
                last = e
                reason = str(getattr(e, "reason", e))
                if "403" in reason or "Tunnel" in reason or "CONNECT" in reason:
                    raise MarketError(blocked_hint(self._host(url)))
                if attempt < retries:
                    SLEEP(2)
                    continue
                raise MarketError("could not reach %s (%s)" % (self._host(url), reason))
        raise MarketError("%s kept failing (%s)" % (self._host(url), last))

    # -- Yahoo
    def yahoo(self, path):
        if self.offline is not None:
            raise MarketError("offline mode: nothing saved for Yahoo %s" % path.split("?")[0])
        last = None
        for attempt in (0, 1):
            saw_429 = False
            for host in YAHOO_HOSTS:
                try:
                    out = self._get(host + path)
                    if self.pause:
                        SLEEP(self.pause)
                    return out
                except urllib.error.HTTPError as e:
                    code = e.code
                    body = ""
                    try:
                        if 400 <= code < 500 and code != 429:
                            body = e.read().decode("utf-8", "replace")
                    finally:
                        e.close()
                    last = "HTTP %d" % code
                    if code == 429:
                        saw_429 = True
                        continue
                    if code >= 500:
                        continue
                    if body.strip().startswith("{"):
                        return body
                    raise MarketError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
                except (urllib.error.URLError, OSError) as e:
                    reason = str(getattr(e, "reason", e))
                    if "403" in reason or "Tunnel" in reason or "CONNECT" in reason:
                        raise MarketError(blocked_hint("query2.finance.yahoo.com"))
                    last = reason
                    continue
            if attempt == 0 and saw_429:
                SLEEP(5)
                continue
            break
        raise MarketError("Yahoo answered %s for %s" % (last, path.split("?")[0]))

    def chart(self, sym, rng="5d", interval="5m"):
        """Yahoo v8 chart for one NSE symbol -> series dict."""
        sym = clean_symbol(sym) if not sym.upper().endswith(".BO") else sym.upper()
        if self.offline is not None:
            doc = self.offline.get("chart:%s:%s" % (sym, interval))
            if doc is None:
                raise MarketError("offline mode: no %s %s chart saved" % (sym, interval))
            return parse_chart(doc, sym)
        path = "/v8/finance/chart/%s?range=%s&interval=%s&includePrePost=false" % (
            urllib.parse.quote(yahoo_symbol(sym), safe=".-"), rng, interval)
        return parse_chart(self.yahoo(path), sym)

    def spark(self, symbols, rng, interval, progress=None):
        """Closes for many symbols, 20 a request. Returns ({sym: series}, failed list)."""
        out, failed = {}, []
        if self.offline is not None:
            for s in symbols:
                try:
                    full = self.chart(s, rng, interval)
                except MarketError:
                    failed.append(s)
                    continue
                n = len(full["t"])                 # like Yahoo's spark: closes and meta only
                out[s] = {"t": full["t"], "c": full["c"], "o": [None] * n, "h": [None] * n, "l": [None] * n,
                          "v": [None] * n, "meta": full["meta"], "close_only": True}
            return out, failed
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i + 20]
            ys = {yahoo_symbol(s): s for s in batch}
            path = "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
                ",".join(urllib.parse.quote(y, safe=".-") for y in ys), rng, interval)
            try:
                got = parse_spark(self.yahoo(path), ys)
            except MarketError as e:
                if i == 0 and "not reachable" in str(e):
                    raise
                got = {}
            for v in got.values():
                v["close_only"] = True
            out.update(got)
            failed.extend(s for s in batch if s not in got)
            if progress:
                progress(min(i + 20, len(symbols)), len(symbols))
        return out, failed

    # -- NSE
    def nse_csv(self, which):
        if self.offline is not None:
            txt = self.offline.get("nse:%s" % which)
            if txt is None:
                raise MarketError("offline mode: no NSE %s list saved" % which)
            return txt
        url = NSE_INDEX_CSV[which]
        hdr = {"Referer": NSE_HOME, "Accept": "text/csv,*/*"}
        try:
            return self.get_text(url, hdr, retries=0)
        except MarketError:
            if not self._nse_primed:          # NSE often wants its cookies first
                self._nse_primed = True
                try:
                    self.get_text(NSE_HOME, {"Accept": "text/html"}, retries=0)
                except MarketError:
                    pass
            return self.get_text(url, hdr, retries=1)

    # -- BSE
    def _bse_headers(self):
        return {"Referer": BSE_HOME, "Origin": BSE_HOME.rstrip("/"), "Accept": "application/json, text/plain, */*"}

    def bse_lookup(self, sym):
        if self.offline is not None:
            txt = self.offline.get("bse_lookup:%s" % sym)
            if txt is None:
                raise MarketError("offline mode: no BSE lookup saved for %s" % sym)
            return txt
        url = "%s/PeerSmartSearch/w?Type=SS&text=%s" % (BSE_API, urllib.parse.quote(sym))
        return self.get_text(url, self._bse_headers()).replace("&nbsp;", " ")

    def bse_announcements(self, code, from_date, to_date):
        if self.offline is not None:
            doc = self.offline.get("bse_ann:%s" % code)
            if doc is None:
                raise MarketError("offline mode: no BSE announcements saved for %s" % code)
            return doc
        q = urllib.parse.urlencode({
            "pageno": 1, "strCat": -1, "subcategory": -1,
            "strPrevDate": from_date.strftime("%Y%m%d"), "strToDate": to_date.strftime("%Y%m%d"),
            "strSearch": "P", "strScrip": code, "strType": "C"})
        txt = self.get_text("%s/AnnSubCategoryGetData/w?%s" % (BSE_API, q), self._bse_headers())
        try:
            return json.loads(txt)
        except ValueError:
            raise MarketError("BSE answered something that is not JSON (it may be blocking scripts)")

    # -- Yahoo headlines
    def rss(self, sym):
        if self.offline is not None:
            txt = self.offline.get("rss:%s" % sym)
            if txt is None:
                raise MarketError("offline mode: no headlines saved for %s" % sym)
            return txt
        return self.get_text(RSS % urllib.parse.quote(yahoo_symbol(sym), safe=".-"))


def _read_any(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load_bundle(path):
    if not os.path.exists(path):
        raise MarketError("offline path %s does not exist" % path)
    files = []
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if name.endswith(".json") or name.endswith(".json.gz"):
                files.append(os.path.join(path, name))
    else:
        files = [path]
    out = {}
    for f in files:
        try:
            doc = json.loads(_read_any(f))
        except ValueError:
            raise MarketError("offline file %s is not JSON" % f)
        if isinstance(doc, dict):
            out.update(doc)
    return out


# ---------------------------------------------------------------- Yahoo parsing

def series_from_result(res):
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else [None] * n
    return {"t": list(t), "o": col("open"), "h": col("high"), "l": col("low"),
            "c": col("close"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_chart(text_or_doc, sym=""):
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise MarketError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise MarketError("Yahoo has no data for %s: %s" % (sym, err.get("description") or "unknown symbol"))
    return series_from_result(res[0])


def parse_spark(text_or_doc, ysyms):
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = doc.get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise MarketError("Yahoo's spark answer is not the shape this tool knows")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        y = (item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or "").upper()
        if y in ysyms:
            out[ysyms[y]] = series_from_result(resp[0])
    return out


# ---------------------------------------------------------------- bars

def intraday_bars(series, cal, now, interval_min=5):
    """Completed regular-session bars, oldest first, in IST.
    Dropped: missing prices, off-grid stamps (Yahoo's live tick), bars outside the
    session, and bars still forming (start + interval after `now`)."""
    step = interval_min * 60
    bars = {}
    dropped = {"no_price": 0, "off_grid": 0, "outside": 0, "forming": 0}
    sessions = {}
    for i, ts in enumerate(series["t"]):
        o, h, l, c, v = (series[k][i] for k in ("o", "h", "l", "c", "v"))
        if ts is None or c is None:
            dropped["no_price"] += 1
            continue
        if None in (o, h, l):
            if not series.get("close_only"):
                dropped["no_price"] += 1
                continue
            o = h = l = c                      # spark answers carry closes only
        ts = int(ts)
        if ts % step:
            dropped["off_grid"] += 1
            continue
        dt = from_ts(ts)
        d = dt.date()
        if d not in sessions:
            sessions[d] = cal.session(d)
        s = sessions[d]
        if not s or dt < s[0] or dt >= s[1]:
            dropped["outside"] += 1
            continue
        if dt + datetime.timedelta(seconds=step) > now:
            dropped["forming"] += 1
            continue
        slot = int((dt - s[0]).total_seconds() // step)
        bars[ts] = {"ts": ts, "date": d.isoformat(), "time": clock(dt), "slot": slot,
                    "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": int(v or 0)}
    return [bars[t] for t in sorted(bars)], dropped


def by_day(bars):
    days = {}
    for b in bars:
        days.setdefault(b["date"], {})[b["slot"]] = b
    return days


def slots_in(cal, d, interval_min=5):
    s = cal.session(d)
    if not s:
        return 0
    return int((s[1] - s[0]).total_seconds() // (interval_min * 60))


def daily_bars(series, cal, now):
    """One bar per IST date, oldest first. Today's bar is left out until the close
    (a partial day would fake every setup); pass a `now` after 15:30 to include it."""
    out = {}
    for i, ts in enumerate(series["t"]):
        o, h, l, c, v = (series[k][i] for k in ("o", "h", "l", "c", "v"))
        if ts is None or c is None:
            continue
        d = from_ts(ts).date()
        s = cal.session(d)
        if s and now < s[1] and d >= now.date():
            continue
        if d > now.date():
            continue
        o = c if o is None else o
        h = max(o, c) if h is None else h
        l = min(o, c) if l is None else l
        out[d.isoformat()] = {"date": d.isoformat(), "o": float(o), "h": float(h), "l": float(l),
                              "c": float(c), "v": int(v or 0)}
    return [out[k] for k in sorted(out)]


def daily_closes(series, cal, now):
    return [(b["date"], b["c"]) for b in daily_bars(series, cal, now)]


# ---------------------------------------------------------------- indicators

def sma(vals, n, i=None):
    i = len(vals) - 1 if i is None else i
    if i + 1 < n:
        return None
    w = vals[i + 1 - n:i + 1]
    return sum(w) / float(n)


def rsi_series(closes, n=14):
    """Wilder's RSI, aligned with closes (None until it has n changes)."""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n

    def val(ag, al):
        if al == 0:
            return 100.0 if ag > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + ag / al)
    out[n] = val(ag, al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = val(ag, al)
    return out


def atr_series(bars, n=14):
    """Wilder's ATR on daily (or intraday) bars with o/h/l/c, aligned with bars."""
    out = [None] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            tr = b["h"] - b["l"]
        else:
            pc = bars[i - 1]["c"]
            tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
        if i + 1 == n:
            out[i] = sum(trs) / n
        elif i + 1 > n:
            out[i] = (out[i - 1] * (n - 1) + tr) / n
    return out


# ---------------------------------------------------------------- daily setups (shared)

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average in an uptrend"),
    ("oversold_bounce", "oversold bounce (counter-trend)"),
]
SETUP_LABEL = dict(SETUPS)
SETUP_SHORT = {"breakout_20d": "20d breakout", "bull_flag": "bull flag",
               "reclaim_ma20": "20-DMA reclaim", "oversold_bounce": "oversold bounce"}


def setups_at(closes, i, rsi=None):
    """Setups that fire on day i, from daily closes only, so the scanner and the
    backtest use one rule. Needs at least 21 closes before i for most rules."""
    hits = []
    c = closes
    if i >= 20 and c[i] > max(c[i - 20:i]):
        hits.append("breakout_20d")
    if i >= 15:
        base, run = c[i - 15], c[i - 5]
        window = c[i - 4:i + 1]
        if base > 0 and (run / base - 1) > 0.10 and (max(window) / min(window) - 1) <= 0.05:
            hits.append("bull_flag")
    if i >= 200:
        m20_now, m20_prev = sma(c, 20, i), sma(c, 20, i - 1)
        m50, m200 = sma(c, 50, i), sma(c, 200, i)
        if c[i - 1] < m20_prev and c[i] > m20_now and m50 > m200:
            hits.append("reclaim_ma20")
    if rsi is not None and i >= 1 and rsi[i - 1] is not None and rsi[i - 1] < 30 and c[i] > c[i - 1]:
        hits.append("oversold_bounce")
    return hits


def context_at(closes, i, rsi=None):
    c = closes
    m50, m200 = sma(c, 50, i), sma(c, 200, i)
    hi = max(c[max(0, i - 251):i + 1])
    return {
        "sma20": _r(sma(c, 20, i)), "sma50": _r(m50), "sma200": _r(m200),
        "above_200dma": (c[i] > m200) if m200 else None,
        "rsi14": _r(rsi[i]) if rsi and rsi[i] is not None else None,
        "off_52w_high_pct": _r((c[i] / hi - 1) * 100) if hi else None,
    }


def _r(x, n=2):
    return None if x is None else round(x, n)


# ---------------------------------------------------------------- small CLI helpers

def add_common(p):
    p.add_argument("--now", help='pin the clock: "YYYY-MM-DD HH:MM" India time')
    p.add_argument("--offline", metavar="PATH", help="read a saved data bundle instead of the internet")
    p.add_argument("--trader-dir", metavar="DIR", help="where trader/*.json live (default ./trader)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    return p


def fail(msg, code=1):
    sys.stdout.write(str(msg).rstrip() + "\n")
    return code


DISCLAIMER = ("Suggestions only, not financial advice. A rule matching is not a prediction; "
              "most intraday traders in India lose money (SEBI, FY23: about 7 in 10).")
