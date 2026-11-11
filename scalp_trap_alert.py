"""BANKNIFTY trap-reversal scalp ALERTS -> Telegram channel "scalping".  Alert only, never places an order.

STATUS OF THE RULE: a CANDIDATE, not proven (found among 25,920 tested combinations; see reports/SCALP_CHECKLIST.md).  Every message says so.

Rule = exactly the tested one (research/scripts/scalp_full_search.py, family SWEEP30, gate FAST, window early):
  FAST day : median high-low range (% of open) of the previous 20 complete sessions >= 1.1
  noise    : median, over the same 20 sessions, of the day's average 1-minute (high-low)
  level    : highest high / lowest low of the 30 minutes that end 4 minutes before the signal minute
  TOP trap : a high above the level within the previous 3 minutes, this minute CLOSES below it, previous close was at/above it -> buy PUT
  LOW trap : mirror -> buy CALL
  window   : signal minute stamped 09:30 .. 10:59 (first possible about 09:49) (candles are stamped with their CLOSE time, 09:16 = first), max 4 a day, >= 20 minutes apart
  exit     : stop 3 x noise (checked first), target 3 x stop, else after 30 minutes

Standalone (requests + cryptography only) so it runs on GitHub Actions or any PC.  Env: DHAN_TOKEN or (TOKEN_ENC_KEY + state/token.enc),
TELEGRAM_BOT_TOKEN, SCALPING_CHAT_ID; DRY_RUN=1 prints instead of sending; FORCE=1 ignores the clock (testing).
"""
import base64
import datetime as dt
import json
import os
import statistics
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
FAST_MIN, STOP_X, TGT_R, HOLD, LOOK, MAXN, GAP = 1.1, 3.0, 3.0, 30, 30, 4, 20
W_START, W_END = 9 * 60 + 30, 11 * 60                  # watch 09:30-11:00 (owner 2026-11-11). The first alert is only possible about 09:49-09:50: the idea needs 30 min of price history after the 09:16 first candle (raw_signal returns nothing before that). Backtest covered signals up to 11:30 (355 trades); 09:50-10:30 subset: 158 trades, mean 0.026%
FIRST, LAST = 9 * 60 + 16, 15 * 60 + 29             # a complete session = 374 one-minute candles
NOTE = "Remember: this idea is not proven yet. Trade very small, or only note it on paper."
DRY = os.environ.get("DRY_RUN", "").lower() in ("1", "true")
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true")


def log(m):
    print("[%s] %s" % (dt.datetime.now(IST).strftime("%H:%M:%S"), m), flush=True)


# ----------------------------------------------------------------------------------------------------------------- pure rule (no network)
def session_stats(c):
    """c = dict of lists mod/open/high/low/close for ONE complete session -> (range % of open, average 1-minute range)."""
    return 100.0 * (max(c["high"]) - min(c["low"])) / c["open"][0], sum(h - l for h, l in zip(c["high"], c["low"])) / len(c["high"])


def gate(prev_sessions):
    """prev_sessions = complete sessions BEFORE today, oldest first.  Returns (trail_range_pct, noise) or (None, None) when < 15 are available."""
    st = [session_stats(c) for c in prev_sessions[-20:]]
    if len(st) < 15:
        return None, None
    return statistics.median(s[0] for s in st), statistics.median(s[1] for s in st)


def raw_signal(c, i):
    """-1 = trap at the top (buy PUT), +1 = trap at the bottom (buy CALL), 0 = nothing, at candle index i.  Uses candles <= i only."""
    if i - 4 - (LOOK - 1) < 0:
        return 0, None
    hi = max(c["high"][i - 4 - LOOK + 1:i - 3]); lo = min(c["low"][i - 4 - LOOK + 1:i - 3])
    mx3 = max(c["high"][i - 3:i]); mn3 = min(c["low"][i - 3:i]); cl, pc = c["close"][i], c["close"][i - 1]
    if mx3 > hi and cl < hi and pc >= hi:
        return -1, hi
    if mn3 < lo and cl > lo and pc <= lo:
        return 1, lo
    return 0, None


def day_signals(c):
    """All alerts of the day so far, recomputed from the first candle (so a restart gives the same list): [(index, direction, level)]."""
    out = []; last = -99
    for i in range(len(c["mod"])):
        if not (W_START <= c["mod"][i] < W_END) or len(out) >= MAXN or i - last < GAP:
            continue
        d, lvl = raw_signal(c, i)
        if d:
            out.append((i, d, lvl)); last = i
    return out


def outcome(c, i, d, noise):
    """Result of the alert at index i with the candles available so far: None while still open, else (kind, index points, minutes)."""
    E = c["close"][i]; stop = STOP_X * noise; tgt = TGT_R * stop
    for j in range(i + 1, min(i + 1 + HOLD, len(c["mod"]))):
        adv = (c["low"][j] - E) if d == 1 else (E - c["high"][j]); fav = (c["high"][j] - E) if d == 1 else (E - c["low"][j])
        if adv <= -stop:
            return "stop", min(-stop, d * (c["open"][j] - E)), j - i
        if fav >= tgt:
            return "target", tgt, j - i
    if len(c["mod"]) > i + HOLD:
        return "time", d * (c["close"][i + HOLD] - E), HOLD
    return None


# ----------------------------------------------------------------------------------------------------------------------------- network
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


_last = [0.0]


def fetch(tok, cid, d_from, d_to):
    """1-minute BANKNIFTY index candles, d_from 09:15 .. d_to 15:30 -> {date: session dict}, candles stamped with their CLOSE minute."""
    for a in range(4):
        w = 0.5 - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()
        try:
            r = requests.post(DH + "/charts/intraday", timeout=30, headers={"access-token": tok, "client-id": cid, "Content-Type": "application/json", "Accept": "application/json"},
                              json={"securityId": BN_ID, "exchangeSegment": BN_SEG, "instrument": BN_INSTR, "interval": "1", "fromDate": d_from + " 09:15:00", "toDate": d_to + " 15:30:00"})
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
    days = {}
    for t, o, h, l, c in zip(j["timestamp"], j["open"], j["high"], j["low"], j["close"]):
        x = dt.datetime.fromtimestamp(t, IST) + dt.timedelta(minutes=STAMP_SHIFT)
        m = x.hour * 60 + x.minute
        if FIRST <= m <= LAST:
            g = days.setdefault(x.strftime("%Y-%m-%d"), dict(mod=[], open=[], high=[], low=[], close=[]))
            if not g["mod"] or m > g["mod"][-1]:
                g["mod"].append(m); g["open"].append(float(o)); g["high"].append(float(h)); g["low"].append(float(l)); g["close"].append(float(c))
    return days


STAMP_SHIFT = int(os.environ.get("STAMP_SHIFT", "0"))   # VERIFIED 2026-11-10 on 2026-11-06: shift 0 matches all 374 stored minutes (shift 1 or -1 only 13); Dhan already stamps the CLOSE minute


def send(text):
    if DRY:
        print("[dry-run] would send:\n" + text + "\n"); return True
    j = requests.post("https://api.telegram.org/bot%s/sendMessage" % os.environ["TELEGRAM_BOT_TOKEN"].strip(), timeout=20,
                      json={"chat_id": os.environ["SCALPING_CHAT_ID"].strip(), "text": text[:4000], "disable_web_page_preview": True}).json()
    if not j.get("ok"):
        log("telegram failed: %s" % str(j.get("description"))[:120])
    return bool(j.get("ok"))


def hhmm(m):
    return "%02d:%02d" % (m // 60, m % 60)


def history(tok, cid, today):
    """Complete sessions before `today`, oldest first (Dhan serves about 5 trading days of minute data per request, so ask week by week)."""
    days = {}; end = dt.date.fromisoformat(today) - dt.timedelta(days=1)
    for _ in range(9):
        start = end - dt.timedelta(days=6)
        try:
            days.update(fetch(tok, cid, start.isoformat(), end.isoformat()))
        except RuntimeError as e:
            log("history %s..%s: %s" % (start, end, e))
        end = start - dt.timedelta(days=1)
        if sum(1 for g in days.values() if len(g["mod"]) == 374) >= 20:
            break
    return [days[d] for d in sorted(days) if d < today and len(days[d]["mod"]) == 374]


def main():
    now = dt.datetime.now(IST); today = now.strftime("%Y-%m-%d")
    if not FORCE and (now.weekday() >= 5 or not (9 * 60 + 20 <= now.hour * 60 + now.minute < W_END)):
        log("outside the working window - nothing to do"); return
    tok, cid = dhan_token()
    t0, got = None, False                                  # market-holiday guard: no candles for today = closed day
    for _try in range(3):
        try:
            t0 = fetch(tok, cid, today, today).get(today); got = True; break
        except RuntimeError as e:
            log(str(e)); time.sleep(20)
    if got and not t0 and not FORCE:
        log("no candles for today - market closed (holiday); no alerts, no message"); return
    prev = history(tok, cid, today); trail, noise = gate(prev)
    if trail is None:
        send("Scalping: I could not check today's market speed because the data did not come in properly. No alerts today. Please do not trade this idea today."); return
    pts = None
    if t0:
        pts = trail / 100.0 * t0["open"][0]
    pts = pts or trail / 100.0 * prev[-1]["close"][-1]
    rng_txt = "%d points" % round(pts)
    if trail < FAST_MIN:
        send("Scalping - %s\nToday: SKIP. The market has been moving slowly lately (a normal day moves only about %s). This idea works only when the market is moving fast.\nI will not send any trade alerts today.\n%s" % (today, rng_txt, NOTE)); return
    stop = STOP_X * noise
    send("Scalping - %s\nToday: OK to watch. The market has been moving fast lately (a normal day moves about %s).\nI will watch from 09:30 to 11:00 and message you if a trap shows up (the first one is possible only after about 09:50, because the idea needs half an hour of price history). At most 4 alerts.\nIf you trade, your safety exit today is %d Bank Nifty points against you (about %d points on the option price).\n%s"
         % (today, rng_txt, round(stop), round(0.55 * stop), NOTE))
    sent, closed, results = set(), set(), []
    while True:
        now = dt.datetime.now(IST); nm = now.hour * 60 + now.minute
        try:
            c = fetch(tok, cid, today, today).get(today)
        except RuntimeError as e:
            log(str(e)); c = None
        if c:
            while c["mod"] and c["mod"][-1] > nm:          # drop the minute that is still forming
                for k in c:
                    c[k].pop()
            sig = day_signals(c)
            for n, (i, d, lvl) in enumerate(sig, 1):
                if i in sent:
                    continue
                sent.add(i); E = c["close"][i]; age = nm - c["mod"][i]; atm = int(round(E / 100.0) * 100)
                side = "Price jumped up and fell back, so it looks like a fake rise.\nIdea: BUY A PUT (bet on a fall)" if d == -1 else "Price dropped down and came back, so it looks like a fake fall.\nIdea: BUY A CALL (bet on a rise)"
                late = "\nThis signal is already %d minutes old. Do not chase it." % age if age >= 2 else ""
                send("Scalping alert %d (max 4 a day) - time %s\n%s\n(nearest strike %d %s)\nBank Nifty is now %.0f.\nSAFETY EXIT: get out if Bank Nifty goes %s %.0f (that is %d points against you).\nTIME EXIT: get out at %s even if nothing happened.%s\n%s"
                     % (n, hhmm(c["mod"][i]), side, atm, "PE" if d == -1 else "CE", E, "above" if d == -1 else "below", E - d * stop, round(stop),
                        hhmm(c["mod"][i] + HOLD), late, NOTE))
            for (i, d, lvl) in sig:
                if i in sent and i not in closed:
                    o = outcome(c, i, d, noise)
                    if o:
                        closed.add(i); results.append(o[1])
                        word = {"stop": "the safety exit was reached (a loss)", "target": "the big profit target was reached (a gain)", "time": "the 30 minutes ended"}[o[0]]
                        send("Result of the %s alert: %s, after %d minutes.\nBank Nifty moved %+.0f points your way (about %+.0f on the option, before charges)." % (hhmm(c["mod"][i]), word, o[2], o[1], 0.55 * o[1]))
            if nm >= W_END and len(closed) == len(sig):
                break
        if nm >= W_END + HOLD + 5 or FORCE and os.environ.get("ONE_PASS"):
            break
        time.sleep(max(2.0, 63 - dt.datetime.now(IST).second))     # wake about 3 s after each minute closes
    send("Scalping - %s is over. Alerts sent: %d.\n%s\n%s" % (today, len(sent), ("Total: %+.0f Bank Nifty points (about %+.0f on the options, before charges)." % (sum(results), 0.55 * sum(results))) if results else "No trap appeared today, so no trade.", NOTE))


if __name__ == "__main__":
    main()
