"""WIDE-DAY 11:00 PULLBACK watch -> Telegram channel "scalping".  Alert only, never places an order.  Runs once at about 11:00 IST.

STATUS: a CANDIDATE from a selective past-data search (main project research/scripts/overnight_2026-11-23/selective_70pct_search.py,
rules fixed before running).  Not yet proven live.  A similar earlier rule won only 1 of 5 live trades -- every message says so.

Rule (exactly the tested one):
  1. yesterday's high-low range was wide: >= WIDE % of the close before it
  2. today the index ROSE from 10:30 to 11:00 by >= RISE %
  -> at the 11:00 one-minute close: idea = a quick FALL (buy an at-the-money PUT / short), target 0.2% lower, stop 0.4% higher,
     otherwise exit at 15:15.  'Stronger' if yesterday also finished >= FIN % above its own open.
Past results (index points after costs, 2021-2026 for Bank Nifty, 2025-2026 for Nifty/Sensex):
  Bank Nifty 1+2: 146 trades, 75% won, +19/trade; stronger: 73 trades, 84% won, +43.  Nifty 1+2: 37, 76%, +21.  Sensex 1+2: 33, 88%, +102.
  Shorting EVERY day at 11:00 with the same exits: 64% won but -3/trade (the conditions are what make it work).
Sends a message ONLY on setup days; silent otherwise and on holidays.

Env: DHAN_TOKEN or (TOKEN_ENC_KEY + state/token.enc), TELEGRAM_BOT_TOKEN, SCALPING_CHAT_ID; DRY_RUN=1 prints; AS_OF=YYYY-MM-DD tests a past day."""
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
T0, T1 = "10:30", "11:00"
TGT, STOP = 0.002, 0.004
# Exchange-published weekday trading holidays (main project config/exchange_published_holidays.yaml). Add the next year's list in December.
HOLIDAYS = {"2026-11-10", "2026-11-24", "2026-12-25"}
# name, id, WIDE %, RISE %, FIN %, strike step, past text
INDEXES = [("Bank Nifty", "25", 1.41, 0.073, 0.33, 100, "146 past trades, 75% won, +19 pts each (stronger version: 73 trades, 84% won, +43)"),
           ("Nifty", "13", 0.976, 0.049, 0.273, 50, "37 past trades, 76% won, +21 pts each"),
           ("Sensex", "51", 0.979, 0.051, 0.281, 100, "33 past trades, 88% won, +102 pts each")]


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


def post(tok, cid, path, body):
    for a in range(4):
        try:
            r = requests.post(DH + path, timeout=30, headers={"access-token": tok, "client-id": cid, "Content-Type": "application/json", "Accept": "application/json"}, json=body)
            if r.status_code in (401, 403):
                raise SystemExit("Dhan rejected the token (%d)" % r.status_code)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(3 * (a + 1)); continue
            j = r.json()
            return j if isinstance(j, dict) and "close" in j else None
        except (requests.Timeout, requests.ConnectionError):
            time.sleep(3 * (a + 1))
    raise RuntimeError("Dhan not reachable after 4 tries")


def minutes(tok, cid, sec, day):
    j = post(tok, cid, "/charts/intraday", {"securityId": sec, "exchangeSegment": "IDX_I", "instrument": "INDEX", "interval": "1", "fromDate": day + " 09:15:00", "toDate": day + " 15:30:00"})
    if not j: return []
    out = [(dt.datetime.fromtimestamp(t, IST), float(o), float(h), float(l), float(c)) for t, o, h, l, c in zip(j["timestamp"], j["open"], j["high"], j["low"], j["close"])]
    return sorted((ts.strftime("%H:%M"), o, h, l, c) for ts, o, h, l, c in out if ts.date().isoformat() == day and c > 0)


def prior_days(tok, cid, sec, day):
    """Last two finished sessions before `day` from one-minute candles: [(open, high, low, close), ...] oldest first."""
    out, d = [], dt.date.fromisoformat(day)
    for _ in range(10):
        d -= dt.timedelta(days=1)
        if d.weekday() >= 5 or d.isoformat() in HOLIDAYS: continue
        b = minutes(tok, cid, sec, d.isoformat())
        if len(b) >= 300:
            out.insert(0, (b[0][1], max(x[2] for x in b), min(x[3] for x in b), b[-1][4]))
            if len(out) == 2: return out
        time.sleep(0.4)
    return out


def send(text):
    if DRY:
        print("[dry-run] would send:\n" + text + "\n"); return True
    j = requests.post("https://api.telegram.org/bot%s/sendMessage" % os.environ["TELEGRAM_BOT_TOKEN"].strip(), timeout=20,
                      json={"chat_id": os.environ["SCALPING_CHAT_ID"].strip(), "text": text[:4000], "disable_web_page_preview": True}).json()
    if not j.get("ok"):
        log("telegram failed: %s" % str(j.get("description"))[:120])
    return bool(j.get("ok"))


def evaluate(name, bars, prev, wide, rise, fin, step, past):
    idx = {b[0]: b for b in bars}
    if T0 not in idx or T1 not in idx or len(prev) < 2:
        return None, "missing data"
    (po, ph, pl, pc), ppc = prev[1], prev[0][3]
    prange = 100 * (ph - pl) / ppc; pfin = 100 * (pc / po - 1)
    c0, c1 = idx[T0][4], idx[T1][4]; r30 = 100 * (c1 / c0 - 1)
    why = "yesterday range %.2f%% (needs %.2f), 10:30->11:00 %+.2f%% (needs +%.2f)" % (prange, wide, r30, rise)
    if prange < wide or r30 < rise:
        return None, why
    strong = pfin >= fin
    strike = round(c1 / step) * step
    return ("%s%s: yesterday was a WIDE day (range %.2f%%) and it rose %.2f%% from 10:30 to 11:00 (now %.0f).\n"
            "   Idea: a quick FALL. Buy the %d PUT (at the money) now, or short.\n"
            "   Take profit if %s falls to about %.0f (0.2%% lower). Exit if it rises to about %.0f (0.4%% higher). Otherwise leave by 15:15.\n"
            "   Past: %s." % (name, " (STRONGER: yesterday also finished strong)" if strong else "", prange, r30, c1, strike, name,
                             c1 * (1 - TGT), c1 * (1 + STOP), past)), why


def main():
    now = dt.datetime.now(IST)
    if AS_OF:
        day = AS_OF
    else:
        if now.weekday() >= 5 or not (10 * 60 + 25 <= now.hour * 60 + now.minute <= 11 * 60 + 8):
            log("outside the 10:25-11:08 window or weekend: nothing to do"); return 0
        wake = now.replace(hour=11, minute=0, second=40, microsecond=0)
        if now < wake:
            log("waiting until 11:00:40 for the decision candle"); time.sleep((wake - now).total_seconds())
        day = now.date().isoformat()
    if day in HOLIDAYS:
        log("%s is a published market holiday: silent" % day); return 0
    tok, cid = dhan_token()
    lines, seen = [], 0
    for name, sec, wide, rise, fin, step, past in INDEXES:
        bars = minutes(tok, cid, sec, day)
        if not bars:
            log("%s: no price data today" % name); continue
        seen += 1
        msg, why = evaluate(name, bars, prior_days(tok, cid, sec, day), wide, rise, fin, step, past)
        log("%s: %s -> %s" % (name, why, "SETUP" if msg else "no setup"))
        if msg: lines.append(msg)
    if not seen:
        log("no price data for %s (holiday?): silent" % day); return 0
    if not lines:
        log("no setup today: silent"); return 0
    when = dt.date.fromisoformat(day).strftime("%d %b")
    text = ("WIDE-DAY 11:00 IDEA - %s (an idea for you to judge, not an order)\n\n" % when + "\n".join(lines) +
            "\n\nWarning: found on past data, NOT yet proven live. Each loss is about twice a win, so it needs to keep winning about 70%+. "
            "A similar earlier rule won only 1 of 5 live trades. Trade very small, or only note it on paper." )
    return 0 if send(text) else 1


if __name__ == "__main__":
    sys.exit(main())
