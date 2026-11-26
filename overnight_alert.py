"""OVERNIGHT watch, RULE v2 (owner 2026-11-26) -> Telegram channel "scalping".  Alert only, never places an order.  Runs once at about 15:20 IST (owner moved it from 15:25 on 2026-11-26).

RULE v2 (main project src/overnight_signal.py --rule v2, reports/OVERNIGHT_SIGNAL_V2_2026-11-26.md), decided with the 15:25 one-minute close:
  STRONG day  : index up at least +0.35% from today's open, AND
  QUIET finish: the last 30 minutes rose less than +0.10%   -> idea: buy the at-the-money CALL now (15:20-15:28), sell it at the next open.
  Strong day that kept pushing up in the last 30 minutes -> skip.   Weak day -> no idea (calls only, owner 2026-11-24).
Past results (decided 15:25, index, after costs): Bank Nifty 2021-26 +0.15% per night (64% won), since Apr 2026 +0.17%; Nifty since Apr 2026 +0.19%,
Sensex +0.26%.  Options since Apr 2026 (close -> next open): Bank Nifty +4.8%, Nifty +12.2%, Sensex +17.0% of the premium per trade.  NOT proven live.
Previous rule (v1, 15:20, per-index cuts, no last-30-minute check) is in git history (commit 60d50e1).
Sends ONE message each trading day (ideas or 'no idea today'); silent on holidays.

Standalone (requests + cryptography).  Env: DHAN_TOKEN or (TOKEN_ENC_KEY + state/token.enc), TELEGRAM_BOT_TOKEN, SCALPING_CHAT_ID;
DRY_RUN=1 prints instead of sending; AS_OF=YYYY-MM-DD evaluates that past day at 15:25 (testing only)."""
import base64
import datetime as dt
import json
import os
import sys
import time
from zoneinfo import ZoneInfo

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

IST = ZoneInfo("Asia/Kolkata")
DH = "https://api.dhan.co/v2"
DRY = os.environ.get("DRY_RUN", "").lower() in ("1", "true")
AS_OF = os.environ.get("AS_OF", "").strip()
DECIDE = "15:20"   # owner 2026-11-26 (was 15:25)
STRONG = 0.35   # % up from today's open
QUIET = 0.10    # the last-30-minute rise must be below this
# Exchange-published weekday trading holidays (main project config/exchange_published_holidays.yaml). Add the next year's list in December.
HOLIDAYS = {"2026-11-10", "2026-11-24", "2026-12-25"}


def next_trading_day(day):
    d = dt.date.fromisoformat(day) + dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in HOLIDAYS:
        d += dt.timedelta(days=1)
    return d
# name, Dhan id, strike step, past result text
INDEXES = [("Bank Nifty", "25", 100, "since Apr 2026 the call made about +4.8% of the premium per trade; 2021-26 the index gained +0.15% per such night, 64% won"),
           ("Nifty", "13", 50, "since Apr 2026 the call made about +12% of the premium per trade"),
           ("Sensex", "51", 100, "since Apr 2026 the call made about +17% of the premium per trade")]


def log(m):
    print("[%s] %s" % (dt.datetime.now(IST).strftime("%H:%M:%S"), m), flush=True)


def dhan_token():
    tok = os.environ.get("DHAN_TOKEN", "").strip()
    if not tok:
        from cryptography.fernet import Fernet
        tok = Fernet(os.environ["TOKEN_ENC_KEY"].strip().encode()).decrypt(open(os.path.join(os.environ.get("STATE_DIR", "state"), "token.enc"), "rb").read()).decode().strip()
    try:
        p = tok.split(".")[1]; cid = str(json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("dhanClientId") or "")
    except Exception:
        cid = ""
    return tok, cid or os.environ.get("DHAN_CLIENT_ID", "").strip()


def minutes(tok, cid, sec_id, day):
    """[(HH:MM, open, close)] for one day, oldest first (Dhan one-minute candles)."""
    for a in range(4):
        try:
            r = requests.post(DH + "/charts/intraday", timeout=30, headers={"access-token": tok, "client-id": cid, "Content-Type": "application/json", "Accept": "application/json"},
                              json={"securityId": sec_id, "exchangeSegment": "IDX_I", "instrument": "INDEX", "interval": "1", "fromDate": day + " 09:15:00", "toDate": day + " 15:30:00"})
            if r.status_code in (401, 403):
                raise SystemExit("Dhan rejected the token (%d)" % r.status_code)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(3 * (a + 1)); continue
            j = r.json()
            if not isinstance(j, dict) or "close" not in j:
                return []
            break
        except (requests.Timeout, requests.ConnectionError):
            time.sleep(3 * (a + 1))
    else:
        raise RuntimeError("Dhan not reachable after 4 tries")
    out = []
    for t, o, c in zip(j["timestamp"], j["open"], j["close"]):
        ts = dt.datetime.fromtimestamp(t, IST)
        if ts.date().isoformat() == day and float(c) > 0:
            out.append((ts.strftime("%H:%M"), float(o), float(c)))
    return sorted(out)


def send(text):
    if DRY:
        print("[dry-run] would send:\n" + text + "\n"); return True
    j = requests.post("https://api.telegram.org/bot%s/sendMessage" % os.environ["TELEGRAM_BOT_TOKEN"].strip(), timeout=20,
                      json={"chat_id": os.environ["SCALPING_CHAT_ID"].strip(), "text": text[:4000], "disable_web_page_preview": True}).json()
    if not j.get("ok"):
        log("telegram failed: %s" % str(j.get("description"))[:120])
    return bool(j.get("ok"))


def evaluate(name, bars, step, nxt="tomorrow's"):
    """Returns (line, has_idea) from one day's one-minute bars, using the last bar at or before 15:20."""
    upto = [b for b in bars if b[0] <= DECIDE]
    if len(upto) < 300:
        return None
    o, c = bars[0][1], upto[-1][2]
    ch = 100 * (c / o - 1)
    l30 = 100 * (c / upto[-31][1] - 1)          # last 30 minutes: open of the bar 30 minutes before the decision bar -> decision close
    strike = round(c / step) * step
    if ch >= STRONG and l30 < QUIET:
        return ("%s: STRONG day, up %.2f%% from today's open (%.0f -> %.0f at %s), quiet last 30 minutes (%+.2f%%).\n"
                "   Idea: buy the %d CALL (at the money) now, sell it at %s open (09:15-09:20)." % (name, ch, o, c, upto[-1][0], l30, strike, nxt), True)
    if ch >= STRONG:
        return ("%s: strong day (%+.2f%%) but the last 30 minutes kept rising (%+.2f%%, needs below +%.2f%%): skip - such nights lost recently." % (name, ch, l30, QUIET), False)
    if ch <= -STRONG:
        return ("%s: weak day (%+.2f%% from today's open): no idea - calls only." % (name, ch), False)
    return ("%s: no idea today (%+.2f%% from today's open; needs %+.2f%% and a quiet last 30 minutes)." % (name, ch, STRONG), False)


def main():
    now = dt.datetime.now(IST)
    if AS_OF:
        day = AS_OF
    else:
        if now.weekday() >= 5 or not (14 * 60 + 50 <= now.hour * 60 + now.minute <= 15 * 60 + 27):
            log("outside the 14:50-15:27 window or weekend: nothing to do"); return 0
        # GitHub's own timer starts late by a few minutes, so the run is scheduled about 15:05 and waits here for the 15:20 candle
        wake = now.replace(hour=15, minute=20, second=40, microsecond=0)
        if now < wake:
            log("waiting until 15:20:40 for the decision candle"); time.sleep((wake - now).total_seconds())
        day = now.date().isoformat()
    if day in HOLIDAYS:
        log("%s is a published market holiday: silent" % day); return 0
    nd = next_trading_day(day)
    nxt = "tomorrow's" if (nd - dt.date.fromisoformat(day)).days == 1 else "the next trading day's (%s)" % nd.strftime("%a %d %b")
    tok, cid = dhan_token()
    lines, ideas, seen = [], 0, 0
    for name, sec, step, past in INDEXES:
        bars = minutes(tok, cid, sec, day)
        if not bars:
            lines.append("%s: no price data today." % name); continue
        seen += 1
        r = evaluate(name, bars, step, nxt)
        if r is None:
            lines.append("%s: not enough of today's prices yet." % name); continue
        lines.append(r[0] + ("\n   Past: " + past + "." if r[1] else ""))
        ideas += r[1]
    if not seen:
        log("no price data for %s (market holiday?): silent" % day); return 0
    when = dt.date.fromisoformat(day).strftime("%d %b")
    head = ("OVERNIGHT IDEA (rule v2) - %s, decided at 15:20 (an idea for you to judge, not an order)\n\n" % when) if ideas else ("OVERNIGHT CHECK (rule v2) - %s: no overnight idea today.\n\n" % when)
    tail = ("\n\nWhy: a day that closes clearly up from its own open WITHOUT a last-minute push has tended to open higher next morning "
            "(Bank Nifty 2021-26 positive in every year). Strong days that pushed up in the last 30 minutes lost recently, so they are skipped. "
            "Maximum loss = the premium paid: keep it to about 1% of capital per trade. NOT yet proven live.") if ideas else ""
    msg = head + "\n".join(lines) + tail
    log("%d idea(s) for %s" % (ideas, day))
    return 0 if send(msg) else 1


if __name__ == "__main__":
    sys.exit(main())
