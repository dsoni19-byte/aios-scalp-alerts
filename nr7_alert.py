"""BANKNIFTY "narrow day" (NR7) WATCH -> Telegram channel "scalping".  Alert only, never places an order.  Runs once after the close (about 16:15 IST).

STATUS OF THE RULE: a CANDIDATE, not proven.  Tested pre-registered on 15 years of daily Dhan data
(research/NR7_DAILY_PREREGISTRATION_2026-11-12.md): BANKNIFTY 549 trades, +0.17% of index price per trade after an assumed cost, 52% wins,
p=0.0004 against ordinary days; weaker recently (+0.12% per trade since 2025, p=0.037).  Every message says so.

Rule = exactly the tested one:
  NR7 day : the just-finished session's high-low range is the smallest of the last 7 sessions (itself included)
  next day: if price trades ABOVE that day's high -> long idea, if BELOW its low -> short idea, exit at the close; stop = the other level;
            if BOTH levels are hit the day is a whipsaw (counted as a full loss in the test).
Sends a message ONLY on NR7 days (about once a week).  Silent on all other days and on market holidays (no new daily bar).

Standalone (requests + cryptography only).  Env: DHAN_TOKEN or (TOKEN_ENC_KEY + state/token.enc), TELEGRAM_BOT_TOKEN, SCALPING_CHAT_ID;
DRY_RUN=1 prints instead of sending; AS_OF=YYYY-MM-DD treats that date as the last finished session (testing/preview only)."""
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
BN_ID, BN_SEG, BN_INSTR = "25", "IDX_I", "INDEX"
DRY = os.environ.get("DRY_RUN", "").lower() in ("1", "true")
AS_OF = os.environ.get("AS_OF", "").strip()


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


def daily_bars(tok, cid, d_from, d_to):
    """[(date, open, high, low, close)] oldest first."""
    for a in range(4):
        try:
            r = requests.post(DH + "/charts/historical", timeout=30, headers={"access-token": tok, "client-id": cid, "Content-Type": "application/json", "Accept": "application/json"},
                              json={"securityId": BN_ID, "exchangeSegment": BN_SEG, "instrument": BN_INSTR, "expiryCode": 0, "fromDate": d_from, "toDate": d_to})
            if r.status_code in (401, 403):
                raise SystemExit("Dhan rejected the token (%d)" % r.status_code)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(3 * (a + 1)); continue
            j = r.json()
            if not isinstance(j, dict) or "close" not in j:
                raise RuntimeError("Dhan reply without candles: %s" % str(j)[:120])
            break
        except (requests.Timeout, requests.ConnectionError):
            time.sleep(3 * (a + 1))
    else:
        raise RuntimeError("Dhan not reachable after 4 tries")
    return [(dt.datetime.fromtimestamp(t, IST).date().isoformat(), float(o), float(h), float(l), float(c))
            for t, o, h, l, c in zip(j["timestamp"], j["open"], j["high"], j["low"], j["close"]) if float(h) >= float(l) > 0]


def send(text):
    if DRY:
        print("[dry-run] would send:\n" + text + "\n"); return True
    j = requests.post("https://api.telegram.org/bot%s/sendMessage" % os.environ["TELEGRAM_BOT_TOKEN"].strip(), timeout=20,
                      json={"chat_id": os.environ["SCALPING_CHAT_ID"].strip(), "text": text[:4000], "disable_web_page_preview": True}).json()
    if not j.get("ok"):
        log("telegram failed: %s" % str(j.get("description"))[:120])
    return bool(j.get("ok"))


def evaluate(bars, last_date):
    """bars up to and including the just-finished session. Returns the message or None."""
    if len(bars) < 8 or bars[-1][0] != last_date:
        return None
    rng = [h - l for _, o, h, l, c in bars]
    if rng[-1] > min(rng[-7:]):
        return None
    d, o, h, l, c = bars[-1]
    nxt = dt.date.fromisoformat(d) + dt.timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    return ("NARROW DAY WATCH - Bank Nifty (an idea for you to judge, not an order)\n\n"
            "%s was Bank Nifty's quietest day of the last 7: high %.0f, low %.0f (range %.0f points).\n\n"
            "Next trading day (about %s), the idea is:\n"
            "- If Bank Nifty trades ABOVE %.0f: the rise may continue. A call idea; leave by the close. Stop idea: if it falls back below %.0f.\n"
            "- If it trades BELOW %.0f: the fall may continue. A put idea; leave by the close. Stop idea: if it rises back above %.0f.\n"
            "- If BOTH levels get touched the same day, it is a choppy day. That is the losing case; do not chase.\n\n"
            "What the test showed (15 years of daily data): about 52 of 100 such trades won, and the average was +0.17%% of the index price per trade (+0.12%% since 2025), about 37 times a year. "
            "Worst single trade lost about 3%%. This does not include real option costs or slippage.\n"
            "Remember: this idea is not proven yet. Trade very small, or only note it on paper.") % (
        dt.date.fromisoformat(d).strftime("%d %b"), h, l, h - l, nxt.strftime("%a %d %b"), h, l, l, h)


def main():
    now = dt.datetime.now(IST)
    today = now.date().isoformat()
    tok, cid = dhan_token()
    frm = (now.date() - dt.timedelta(days=40)).isoformat()
    bars = daily_bars(tok, cid, frm, (now.date() + dt.timedelta(days=1)).isoformat())
    if AS_OF:
        bars = [b for b in bars if b[0] <= AS_OF]; last = AS_OF
    else:
        if now.weekday() >= 5 or now.hour * 60 + now.minute < 15 * 60 + 45:
            log("too early or weekend: the day's bar is not final yet; nothing to do"); return 0
        last = today
        bars = [b for b in bars if b[0] <= today]
    if not bars or bars[-1][0] != last:
        log("no daily bar for %s (market holiday or data not ready): silent" % last); return 0
    msg = evaluate(bars, last)
    if not msg:
        log("%s is not a narrow (NR7) day: silent (range %.0f, last 7 ranges min %.0f)" % (last, bars[-1][2] - bars[-1][3], min(b[2] - b[3] for b in bars[-7:]))); return 0
    log("NR7 day %s -> message" % last)
    return 0 if send(msg) else 1


if __name__ == "__main__":
    sys.exit(main())
