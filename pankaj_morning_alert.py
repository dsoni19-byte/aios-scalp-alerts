"""BANKNIFTY morning option-premium ALERTS in the style taught by Pankaj Sahu -> Telegram channel "pankaj sahu".  Alert only, never places an order.

STATUS: a mechanical READING of what he says in his videos (vault: Trading/Pankaj Sahu), NOT his indicator and NOT a tested system.
The real "Loren" indicator is his private code.  The only disclosed part is the blue line = 39 EMA on the 5-minute Bank Nifty chart
(he confirmed it to a viewer in a live); his custom RSI, trend lines and hand-drawn liquidity zones cannot be reproduced and are NOT used.
Full write-up: reports/PANKAJ_SAHU_MORNING_STRATEGY_2026-11-14.md.  Every message says the idea is unproven.

Rule (all times IST, candles stamped with their CLOSE minute, 09:16 = first 1-minute candle, 09:20 = first 5-minute candle):
  side    : last closed 5-minute Bank Nifty candle closes ABOVE the 39 EMA -> CALL side only; BELOW -> PUT side only
  strike  : on each side the monthly-expiry strike whose premium is nearest 700 (band 600-850: his live premiums Oct-Nov 2026), read just after the open
  zones   : on that option's own 1-minute premium chart: yesterday's high / low / close, today's open, first 5 minutes' high / low / middle
  entry   : between 09:21 and 10:30 a GREEN 1-minute premium candle CLOSES above a zone it was at/below one candle earlier,
            and has not already run more than 15 points past the zone (he does not chase a running candle); the side must agree
  gap day : open more than 300 points away from yesterday's close -> entries only 09:30-09:45 (price must come back first), otherwise no trade
  expiry  : no trade on the first session after a monthly expiry (he waits for premiums to settle); expiry day itself = warning, small premium
  target  : +40, but +30 when more than 10 days remain to expiry (early-cycle premium moves slowly)
  exit    : stop 30 premium points; at +20 move the stop to the entry price (cost-to-cost); first target +40 (or +30); all out by 11:00
  one trade a day; after it ends (profit, cost or stop) nothing more is sent.

Standalone (requests + cryptography).  Env: DHAN_TOKEN or (TOKEN_ENC_KEY + state/token.enc), TELEGRAM_BOT_TOKEN, PANKAJ_CHAT_ID;
DRY_RUN=1 prints instead of sending; AS_OF=YYYY-MM-DD replays a past day in one pass (with DRY_RUN); FORCE=1 ignores the clock.
"""
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
BN_ID = 25
EMA_N = 39
PREM_TARGET, PREM_LO, PREM_HI = 700.0, 600.0, 850.0   # his live premiums Oct-Nov 2026: median ~735 (range 450-960); his Dec 2025 band was 550-650
GAP_MAX, GAP_FROM, GAP_DEADLINE = 300.0, 9 * 60 + 30, 9 * 60 + 45
E_START, E_END, T_EXIT = 9 * 60 + 21, 10 * 60 + 30, 11 * 60
STOP, COST_AT, TARGET, CHASE = 30.0, 20.0, 40.0, 15.0
SLOW_DTE, SLOW_TARGET = 10, 30.0        # early in the monthly cycle he expects only 20-30 points (at most 50) a move -> first target 30
POST_EXPIRY_SKIP = 1          # he said 2 sessions (Dec 2025) but traded the 2nd session after expiry on 29 Oct 2026 and the 1st on 30 Sep; day-1 skip kept
MONTHLY_EXPIRIES = ["2026-11-23", "2026-12-29", "2027-01-25"]    # he skips the 2 sessions AFTER each one; update when Dhan lists new ones
NOTE = "Remember: this is my reading of his videos, not his own software, and it is not proven. Trade very small, or only note it on paper."
DRY = os.environ.get("DRY_RUN", "").lower() in ("1", "true")
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true")
AS_OF = os.environ.get("AS_OF", "").strip()


def log(m):
    print("[%s] %s" % (dt.datetime.now(IST).strftime("%H:%M:%S"), m), flush=True)


def hhmm(m):
    return "%02d:%02d" % (m // 60, m % 60)


# ----------------------------------------------------------------------------------------------------------------- pure rule (no network)
def ema_series(closes, n=EMA_N):
    out = []; k = 2.0 / (n + 1); e = None
    for i, c in enumerate(closes):
        e = c if e is None else (c - e) * k + e
        out.append(e if i >= n - 1 else None)
    return out


def side_at(five, m):
    """five = list of (date, mod, close, ema) for 5-minute candles of TODAY, oldest first.  Side from the last candle closed at or before minute m."""
    last = None
    for (mod, close, e) in five:
        if mod <= m and e is not None:
            last = (close, e)
    if last is None:
        return None
    return "CE" if last[0] > last[1] else "PE" if last[0] < last[1] else None


def pick_strike(prem):
    """prem = {strike: premium}.  The strike whose premium is nearest 600; inside 550-650 when one exists."""
    ok = {k: v for k, v in prem.items() if v and v > 0}
    if not ok:
        return None
    band = {k: v for k, v in ok.items() if PREM_LO <= v <= PREM_HI}
    src = band or ok
    return min(src, key=lambda k: abs(src[k] - PREM_TARGET))


def zones(prev, c):
    """prev = yesterday's session of this option (or None); c = today's 1-minute candles so far.  -> {name: level}."""
    z = {}
    if prev and prev["high"]:
        z["yesterday's high"] = max(prev["high"]); z["yesterday's low"] = min(prev["low"]); z["yesterday's close"] = prev["close"][-1]
    if c and c["mod"]:
        z["today's open"] = c["open"][0]
        f = [i for i, m in enumerate(c["mod"]) if m <= 9 * 60 + 20]
        if f and c["mod"][-1] >= 9 * 60 + 20:
            hi = max(c["high"][i] for i in f); lo = min(c["low"][i] for i in f)
            z["first 5 minutes' high"] = hi; z["first 5 minutes' low"] = lo; z["first 5 minutes' middle"] = (hi + lo) / 2.0
    return z


def find_entry(c, z, side, five, gap_big):
    """First valid entry on this option today: (index, zone name, level) or None.  Uses candles <= index only."""
    for i in range(1, len(c["mod"])):
        m = c["mod"][i]
        if m < (GAP_FROM if gap_big else E_START) or m > (GAP_DEADLINE if gap_big else E_END):
            continue
        if c["mod"][i - 1] != m - 1 or c["close"][i] <= c["open"][i] or side_at(five, m) != side:
            continue
        hit = [(lvl, name) for name, lvl in z.items() if c["close"][i - 1] <= lvl < c["close"][i] and c["close"][i] - lvl <= CHASE]
        if hit:
            lvl, name = max(hit)
            return i, name, lvl
    return None


def outcome(c, i):
    """None while open, else (kind, premium points, minutes).  Stop is checked before the target inside a candle (the cautious order)."""
    E = c["close"][i]; stop = E - STOP; at_cost = False
    for j in range(i + 1, len(c["mod"])):
        if c["mod"][j] > T_EXIT:
            break
        if c["low"][j] <= stop:
            return ("cost" if at_cost else "stop"), (0.0 if at_cost else -STOP), c["mod"][j] - c["mod"][i]
        if c["high"][j] >= E + TARGET:
            return "target", TARGET, c["mod"][j] - c["mod"][i]
        if c["high"][j] >= E + COST_AT and not at_cost:
            at_cost = True; stop = E
        if c["mod"][j] == T_EXIT:
            return "time", c["close"][j] - E, c["mod"][j] - c["mod"][i]
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


_last = {"data": 0.0, "chain": 0.0}


def post(path, body, tok, cid, kind="data"):
    gap = 3.3 if kind == "chain" else 0.5
    for a in range(4):
        w = gap - (time.time() - _last[kind])
        if w > 0:
            time.sleep(w)
        _last[kind] = time.time()
        try:
            r = requests.post(DH + path, timeout=30, json=body, headers={"access-token": tok, "client-id": cid, "Content-Type": "application/json", "Accept": "application/json"})
            if r.status_code in (401, 403):
                raise SystemExit("Dhan rejected the token (%d)" % r.status_code)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(3 * (a + 1)); continue
            return r.json()
        except (requests.Timeout, requests.ConnectionError):
            time.sleep(3 * (a + 1))
    raise RuntimeError("Dhan not reachable after 4 tries")


def candles(tok, cid, sec_id, option, interval, d_from, d_to):
    """-> {date: dict(mod, open, high, low, close)}; an empty dict when Dhan has no candles for the range."""
    j = post("/charts/intraday", {"securityId": str(sec_id), "exchangeSegment": "NSE_FNO" if option else "IDX_I", "instrument": "OPTIDX" if option else "INDEX",
                                  "interval": str(interval), "fromDate": d_from + " 09:15:00", "toDate": d_to + " 15:30:00"}, tok, cid)
    days = {}
    if not isinstance(j, dict) or "close" not in j:
        return days
    for t, o, h, l, c in zip(j["timestamp"], j["open"], j["high"], j["low"], j["close"]):
        x = dt.datetime.fromtimestamp(t, IST); m = x.hour * 60 + x.minute
        if 9 * 60 + 16 <= m <= 15 * 60 + 30:
            g = days.setdefault(x.strftime("%Y-%m-%d"), dict(mod=[], open=[], high=[], low=[], close=[]))
            if not g["mod"] or m > g["mod"][-1]:
                g["mod"].append(m); g["open"].append(float(o)); g["high"].append(float(h)); g["low"].append(float(l)); g["close"].append(float(c))
    return days


def cut(c, nm):
    """Drop candles stamped after minute nm (still forming, or - in a replay - the future)."""
    if not c:
        return None
    n = sum(1 for m in c["mod"] if m <= nm)
    return {k: v[:n] for k, v in c.items()}


def chain(tok, cid):
    """Nearest monthly expiry -> (expiry, spot, {strike: {'CE': (security_id, last_price), 'PE': (...)}})."""
    exps = post("/optionchain/expirylist", {"UnderlyingScrip": BN_ID, "UnderlyingSeg": "IDX_I"}, tok, cid, "chain").get("data") or []
    today = (AS_OF or dt.datetime.now(IST).strftime("%Y-%m-%d"))
    exps = [e for e in exps if e >= today]
    if not exps:
        raise RuntimeError("no live expiry returned")
    oc = post("/optionchain", {"UnderlyingScrip": BN_ID, "UnderlyingSeg": "IDX_I", "Expiry": exps[0]}, tok, cid, "chain").get("data") or {}
    if not oc.get("oc"):
        raise RuntimeError("empty option chain")
    out = {}
    for k, v in oc["oc"].items():
        row = {}
        for s in ("ce", "pe"):
            d = v.get(s) or {}
            if d.get("security_id"):
                row[s.upper()] = (int(d["security_id"]), float(d.get("last_price") or 0))
        if row:
            out[int(float(k))] = row
    return exps[0], float(oc.get("last_price") or 0), out


def send(text):
    if DRY:
        print("[dry-run] would send:\n" + text + "\n"); return True
    j = requests.post("https://api.telegram.org/bot%s/sendMessage" % os.environ["TELEGRAM_BOT_TOKEN"].strip(), timeout=20,
                      json={"chat_id": os.environ["PANKAJ_CHAT_ID"].strip(), "text": text[:4000], "disable_web_page_preview": True}).json()
    if not j.get("ok"):
        log("telegram failed: %s" % str(j.get("description"))[:120])
    return bool(j.get("ok"))


def five_today(tok, cid, today, nm):
    """5-minute Bank Nifty candles of the last ~10 days -> today's [(mod, close, ema39)] up to minute nm, plus yesterday's close."""
    start = (dt.date.fromisoformat(today) - dt.timedelta(days=12)).isoformat()
    days = candles(tok, cid, BN_ID, False, 5, start, today)
    seq = [(d, m, c) for d in sorted(days) if d <= today for m, c in zip(days[d]["mod"], days[d]["close"]) if d < today or m <= nm]
    em = ema_series([x[2] for x in seq])
    prev = [x[2] for x in seq if x[0] < today]
    return [(m, c, e) for (d, m, c), e in zip(seq, em) if d == today], (prev[-1] if prev else None)


def opt_days(tok, cid, sec_id, today):
    start = (dt.date.fromisoformat(today) - dt.timedelta(days=7)).isoformat()
    days = candles(tok, cid, sec_id, True, 1, start, today)
    pd = [d for d in sorted(days) if d < today]
    return (days[pd[-1]] if pd else None), days.get(today)


def plan_text(today, gap, gap_big, side, exp, dte, picks, zs):
    lines = ["Pankaj Sahu style - Bank Nifty morning plan - %s" % today]
    lines.append("Opening: %s%d points %s yesterday's close." % ("about " if abs(gap) >= 1 else "", abs(round(gap)), "above" if gap >= 0 else "below"))
    if gap_big:
        lines.append("That is a BIG gap (more than 300). His rule: no trade unless price comes back into the zone; I accept an entry only between 09:30 and 09:45, otherwise no trade today.")
    lines.append("Side right now: %s (Bank Nifty is %s the 39-EMA line on the 5-minute chart). I follow the line if it changes."
                 % (("CALL side only" if side == "CE" else "PUT side only" if side == "PE" else "not clear yet"), "above" if side == "CE" else "below" if side == "PE" else "on"))
    lines.append("Expiry %s is %s. %s" % (exp, "TODAY" if dte == 0 else "%d days away" % dte, "EXPIRY DAY: premiums swing 50-60 points a candle and a 30-point stop can go in one candle. He trades it only in a small size, 9:15-10:00." if dte == 0 else ("Early in the cycle premiums move slowly, so the first target is 30 points." if dte > SLOW_DTE else "")))
    lines.append("Options chosen (premium nearest 700):")
    for s in ("CE", "PE"):
        if picks.get(s):
            k, p = picks[s]
            zz = ", ".join("%s %.0f" % (n, v) for n, v in sorted(zs[s].items(), key=lambda x: -x[1]))
            lines.append("  %d %s, premium now about %.0f. Zones on its chart: %s." % (k, s, p, zz or "not available"))
    lines.append("I watch until 10:30 and send ONE entry at most: a green 1-minute candle closing above a zone. Stop 30 points, first target %d." % TARGET)
    lines.append(NOTE)
    return "\n".join(lines)


def entry_text(k, s, c, i, name, lvl):
    E = c["close"][i]
    return ("ENTRY idea - time %s\nBUY %d %s (bet on a %s) near %.0f.\nReason: the premium closed back above %s (%.0f) and Bank Nifty is on the %s side of the 39-EMA line.\n"
            "STOP: get out if the premium falls to %.0f (30 points).\nWhen it reaches %.0f (+20), move your stop to %.0f so you cannot lose.\nFIRST TARGET: %.0f (+%d). After that keep raising the stop behind the price.\n"
            "TIME EXIT: out by 11:00. Only one trade today.\n%s"
            % (hhmm(c["mod"][i]), k, s, "rise" if s == "CE" else "fall", E, name, lvl, "upper" if s == "CE" else "lower", E - STOP, E + COST_AT, E, E + TARGET, TARGET, NOTE))


def result_text(o, m):
    word = {"stop": "the stop was hit (a loss of 30 points)", "cost": "it went up 20, then came back to the entry price (no loss, no gain)",
            "target": "the first target was reached (+%d points)" % TARGET, "time": "11:00 came (%+.0f points)" % o[1]}[o[0]]
    return "Result of the %s entry: %s, after %d minutes. Before charges. No more trades today." % (hhmm(m), word, o[2])


def main():
    now = dt.datetime.now(IST); today = AS_OF or now.strftime("%Y-%m-%d")
    live = not AS_OF
    if live and not FORCE and (now.weekday() >= 5 or not (9 * 60 <= now.hour * 60 + now.minute < E_END)):
        log("outside the working window - nothing to do"); return
    tok, cid = dhan_token()
    while live and not FORCE and dt.datetime.now(IST).strftime("%H:%M:%S") < "09:21:05":      # the plan needs the first 5-minute candle
        time.sleep(5)
    nm = (dt.datetime.now(IST).hour * 60 + dt.datetime.now(IST).minute) if live else T_EXIT
    plan_nm = nm if live else 9 * 60 + 21
    five, prev_close = five_today(tok, cid, today, nm)
    idx = candles(tok, cid, BN_ID, False, 1, today, today).get(today)
    if not idx or not five:
        log("no candles for today - market closed (holiday); no message"); return
    gap = idx["open"][0] - prev_close if prev_close else 0.0
    gap_big = abs(gap) > GAP_MAX
    for x in MONTHLY_EXPIRIES:
        d0 = dt.date.fromisoformat(x); k = 0; dd = d0
        while k < POST_EXPIRY_SKIP:
            dd += dt.timedelta(days=1)
            if dd.weekday() < 5:
                k += 1
                if dd.isoformat() == today:
                    send("Pankaj Sahu style - %s: NO TRADE today. The monthly expiry was %s and he does not trade the first session after it, because premiums are still unsettled. No alerts today." % (today, x)); return
    exp, spot, oc = chain(tok, cid)
    global TARGET
    dte = (dt.date.fromisoformat(exp) - dt.date.fromisoformat(today)).days
    if dte > SLOW_DTE:
        TARGET = SLOW_TARGET
    near = sorted(oc, key=lambda k: abs(k - idx["close"][0]))[:25]
    picks, data = {}, {}
    for s in ("CE", "PE"):
        if live:
            prem = {k: oc[k][s][1] for k in near if s in oc[k]}
        else:                                                    # replay: the premium of that morning = close of the day's first 1-minute candle
            prem = {}
            for k in near:
                if s in oc[k]:
                    g = candles(tok, cid, oc[k][s][0], True, 1, today, today).get(today)
                    if g and g["mod"][0] <= 9 * 60 + 17:
                        prem[k] = g["close"][0]
        k = pick_strike(prem)
        if k is None:
            continue
        picks[s] = (k, prem[k]); data[s] = opt_days(tok, cid, oc[k][s][0], today)
    if not picks:
        send("Pankaj Sahu style - %s: I could not read the option prices this morning, so no plan and no alert today." % today); return
    zs = {s: zones(data[s][0], cut(data[s][1], plan_nm)) for s in picks}
    send(plan_text(today, gap, gap_big, side_at(five, plan_nm), exp, dte, picks, zs))
    found = None; res = None
    while True:
        if live:
            now = dt.datetime.now(IST); nm = now.hour * 60 + now.minute
            try:
                five, _ = five_today(tok, cid, today, nm)
                for s in picks:
                    t = candles(tok, cid, oc[picks[s][0]][s][0], True, 1, today, today).get(today)
                    if t:
                        data[s] = (data[s][0], t)
            except RuntimeError as e:
                log(str(e))
        if not found:
            best = None
            for s in picks:
                c = cut(data[s][1], nm)
                if c:
                    e = find_entry(c, zs[s], s, five, gap_big)
                    if e and (best is None or c["mod"][e[0]] < best[2]["mod"][best[1][0]]):
                        best = (s, e, c)
            if best:
                s, e, c = best; found = (s, c["mod"][e[0]])
                age = nm - c["mod"][e[0]] if live else 0
                send(entry_text(picks[s][0], s, c, e[0], e[1], e[2]) + ("\nThis signal is already %d minutes old. Do not chase it." % age if age >= 2 else ""))
        if found and not res:
            c = cut(data[found[0]][1], nm); i = c["mod"].index(found[1])
            res = outcome(c, i)
            if res:
                send(result_text(res, found[1]))
        if res or (not found and nm > (GAP_DEADLINE if gap_big else E_END)) or nm > T_EXIT + 2 or not live or (FORCE and os.environ.get("ONE_PASS")):
            break
        time.sleep(max(2.0, 64 - dt.datetime.now(IST).second))
    if not found:
        send("Pankaj Sahu style - %s is over. No clean entry came%s, so no trade today. In his words, a no-trade day is also a good day.\n%s"
             % (today, " between 09:30 and 09:45 after the big gap" if gap_big else " by 10:30", NOTE))
    elif not res:
        send("Pankaj Sahu style - %s: the watch ended before the trade finished. Please manage the exit yourself (out by 11:00)." % today)


if __name__ == "__main__":
    main()
