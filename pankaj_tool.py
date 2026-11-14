"""Pankaj Sahu style BANKNIFTY morning TOOL -> Telegram channel "pankaj sahu".  Alert only, never places an order.  NOT proven (see pankaj_core.py).

 09:21  PLAN message: side (Loren on the index), two strikes (premium nearest 700), the marked LINES/ZONES on each strike's chart, the trigger
        premium to buy at (pullback into a zone, buy only when a 1-minute candle CLOSES back above it) and the stop.
 09:21-10:30 every minute: re-draws the lines; sends  TRIGGER updates (he lowers the trigger as price drifts), ENTRY (pullback or breakout),
        MANAGE (stop to cost at +25, first target, trailing), EXIT; and MIND-CHANGE messages: the market runs one way -> new strike + new
        lines; Loren side flips -> other side.  One trade a day, one re-entry allowed if stopped at cost.
Env: DHAN_TOKEN | TOKEN_ENC_KEY+state/token.enc, DHAN_CLIENT_ID, TELEGRAM_BOT_TOKEN, PANKAJ_CHAT_ID; DRY_RUN=1 print only;
AS_OF=YYYY-MM-DD replay a past day minute by minute (no look-ahead); FORCE_STRIKES=ce,pe force strikes in a replay; FORCE=1 ignore clock.
"""
import datetime as dt
import os
import sys
import time

import pankaj_core as K
import pankaj_morning_alert as pm      # helpers only: dhan_token, post, chain, send, IST

IST = pm.IST
DRY = pm.DRY
AS_OF = os.environ.get("AS_OF", "").strip()
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true")
GAP_MAX = 400.0
E_END, T_HARD = 10 * 60 + 30, 10 * 60 + 35
PREM_TARGET = 700.0
NOTE = "My reading of his method, not his software, not proven. Trade very small; you place the order yourself."
log, hhmm = pm.log, K.hhmm
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def candles(tok, cid, sec_id, option, interval, d_from, d_to):
    """-> {date: dict(mod, open, high, low, close)}; START-minute stamps.  Options drop the 09:15 auction candle (first = 09:16)."""
    first = 9 * 60 + (16 if option else 15)
    j = pm.post("/charts/intraday", {"securityId": str(sec_id), "exchangeSegment": "NSE_FNO" if option else "IDX_I", "instrument": "OPTIDX" if option else "INDEX",
                                     "interval": str(interval), "fromDate": d_from + " 09:15:00", "toDate": d_to + " 15:30:00"}, tok, cid)
    days = {}
    if isinstance(j, dict) and j.get("errorCode"):
        raise RuntimeError("Dhan error %s %s" % (j.get("errorCode"), str(j.get("errorMessage"))[:60]))
    if not isinstance(j, dict) or "close" not in j:
        return days
    for t, o, h, l, c in zip(j["timestamp"], j["open"], j["high"], j["low"], j["close"]):
        x = dt.datetime.fromtimestamp(t, IST); m = x.hour * 60 + x.minute
        if first <= m <= 15 * 60 + 29:
            g = days.setdefault(x.strftime("%Y-%m-%d"), dict(mod=[], open=[], high=[], low=[], close=[]))
            if not g["mod"] or m > g["mod"][-1]:
                g["mod"].append(m); g["open"].append(float(o)); g["high"].append(float(h)); g["low"].append(float(l)); g["close"].append(float(c))
    return days


class Feed:
    """Option / index candles.  Live: refetch every minute.  Replay (AS_OF): fetch once, the loop cuts at the simulated minute (no look-ahead)."""
    def __init__(self, tok, cid, today):
        self.tok, self.cid, self.today = tok, cid, today
        self.cache = {}

    def opt(self, sec_id, refresh=False):
        if sec_id not in self.cache or (refresh and not AS_OF):
            start = (dt.date.fromisoformat(self.today) - dt.timedelta(days=7)).isoformat()
            self.cache[sec_id] = candles(self.tok, self.cid, sec_id, True, 1, start, self.today)
        d = self.cache[sec_id]
        pd = [x for x in sorted(d) if x < self.today]
        return (d[pd[-1]] if pd else None), d.get(self.today)

    def index5_closes(self, nm):
        """12 days of 5-minute Bank Nifty closes up to the last CLOSED bar at clock minute nm; today's open; yesterday's close."""
        key = "idx5"
        if key not in self.cache or not AS_OF:
            start = (dt.date.fromisoformat(self.today) - dt.timedelta(days=14)).isoformat()
            self.cache[key] = candles(self.tok, self.cid, pm.BN_ID, False, 5, start, self.today)
        d = self.cache[key]
        seq = [(x, m, c) for x in sorted(d) if x <= self.today for m, c in zip(d[x]["mod"], d[x]["close"]) if x < self.today or m + 5 <= nm]
        opens = d.get(self.today, {}).get("open", [])
        prevc = [s[2] for s in seq if s[0] < self.today]
        return seq, (opens[0] if opens else None), (prevc[-1] if prevc else None)

    def index1(self):
        if "idx1" not in self.cache or not AS_OF:
            self.cache["idx1"] = candles(self.tok, self.cid, pm.BN_ID, False, 1, self.today, self.today).get(self.today)
        return self.cache["idx1"]


def side_of(seq, today_open, nm):
    """Side from Loren: at the open, index open vs the last kernel value of the PREVIOUS bar -> CE above, PE below.  Later: two closed
    5-minute closes on the other side of the kernel line flip it.  -> (side, kernel_now, rising)."""
    closes = [s[2] for s in seq]
    ker = K.kernel(closes)
    today_idx = [i for i, s in enumerate(seq) if s[0] == (AS_OF or dt.datetime.now(IST).strftime("%Y-%m-%d"))]
    if not today_idx or ker[today_idx[0] - 1] is None or today_open is None:
        return None, None, None
    side = "CE" if today_open > ker[today_idx[0] - 1] else "PE"
    for i in today_idx[2:]:
        a, b = closes[i - 1] - ker[i - 1], closes[i] - ker[i]
        if a > 0 and b > 0:
            side = "CE"
        elif a < 0 and b < 0:
            side = "PE"
    return side, ker[-1], (ker[-1] > ker[-2] if ker[-2] is not None else None)


def premiums(feed, oc, near, s, nm, live):
    if live:
        return {k: oc[k][s][1] for k in near if s in oc[k]}
    out = {}
    for k in near:
        if s in oc[k]:
            c = K.closed(feed.opt(oc[k][s][0])[1], nm)
            if c and c["mod"]:
                out[k] = c["close"][-1]
    return out


def lines_block(name, k, s, prem, zs):
    below = sorted([z for z in zs if z["price"] <= prem], key=lambda z: -z["price"])[:4]
    above = sorted([z for z in zs if z["price"] > prem], key=lambda z: z["price"])[:4]
    t = ["%d %s (premium now %.0f):" % (k, s, prem)]
    t += ["  above: " + zone_line(z) for z in reversed(above)] or ["  above: none marked"]
    t += ["  >> price %.0f" % prem]
    t += ["  below: " + zone_line(z) for z in below] or ["  below: none marked"]
    return "\n".join(t)


def zone_line(z):
    return "%.0f%s - %s" % (z["price"], " **STRONG**" if z["strength"] >= 3 else "", ", ".join(z["names"][:4]))


def trigger_text(side, k, prem, zs, hi_so_far):
    below = [z for z in zs if z["price"] <= prem - 8 and z["price"] <= hi_so_far - K.PULL]
    if not below:
        return None, None
    z = max(below, key=lambda z: (z["strength"] >= 3, z["price"]))
    if z["strength"] < 3:
        z = max(below, key=lambda z: z["price"])
    stop = z["price"] - 30.0
    return z, ("BUY PLAN %d %s: wait for the premium to FALL back to about %.0f (%s). Buy ONLY when a 1-minute candle CLOSES back above %.0f. "
               "Stop about %.0f (30 under the zone). If it never comes back, no trade - do not chase." % (k, side, z["price"], ", ".join(z["names"][:3]), z["price"] + 3, stop))


def plan_msg(today, gap, side, ker_rising, exp, dte, picks, zs, prem, hi, gap_big):
    tx = ["Pankaj Sahu style - Bank Nifty morning lines - %s" % today]
    tx.append("Opening: %d points %s yesterday's close. Loren line says: %s%s." % (abs(round(gap)), "above" if gap >= 0 else "below", "CALL side" if side == "CE" else "PUT side",
              "" if ker_rising is None else (" (line rising)" if ker_rising else " (line falling)")))
    if gap_big:
        tx.append("BIG GAP (more than 400): he normally takes NO trade - no zone/range around. I only watch for a small 20-30 point trade if the premium falls back to today's open, 09:30-10:00.")
    if dte == 0:
        tx.append("EXPIRY DAY: premiums move 50-60 points a candle; one candle can hit the stop. Tiny size only.")
    elif dte > 10:
        tx.append("Expiry %s is %d days away: premium moves slowly now (20-30 points), first target kept small." % (exp, dte))
    for s in ("CE", "PE"):
        if s in picks:
            tx.append("\n" + lines_block("", picks[s][0], s, prem[s], zs[s]))
    z, t = trigger_text(side, picks[side][0], prem[side], zs[side], hi[side]) if side in picks else (None, None)
    tx.append("\n" + (t or "%s side: price is sitting at/under its lines already - I will tell you when a candle closes back above one." % side))
    tx.append("Zone = 'strong' when 3 or more lines sit together (yesterday close/low/high, 5- and 15-minute swings, today's open, round numbers). Hand-drawn trend lines are NOT included - add yours.")
    tx.append(NOTE)
    return "\n".join(tx)


def entry_msg(side, k, e, stopmsg_target):
    t, z = stopmsg_target
    tx = ["ENTRY at %s - BUY %d %s near %.0f   [%s]" % (hhmm(e["t"] + 1), k, side, e["entry"], "pullback into a zone" if e["kind"] == "pullback" else "BREAKOUT - his second plan, smaller size")]
    tx.append("Why: premium closed back above %s." % zone_line(e["zone"]))
    tx.append("STOP %.0f (%.0f points). At +25 I will tell you to move it to cost. FIRST TARGET %.0f%s." % (e["stop"], e["entry"] - e["stop"], t, (" (next zone: %s)" % ", ".join(z["names"][:2])) if z else ""))
    tx.append("Window: 15-20 minutes, then out. One trade today (one re-entry only if it stops at cost).")
    tx.append(NOTE)
    return "\n".join(tx)


def main():
    now = dt.datetime.now(IST); today = AS_OF or now.strftime("%Y-%m-%d"); live = not AS_OF
    if live and not FORCE and (now.weekday() >= 5 or not (9 * 60 <= now.hour * 60 + now.minute < E_END)):
        log("outside the working window"); return
    tok, cid = pm.dhan_token()
    feed = Feed(tok, cid, today)
    if live and not FORCE:
        while dt.datetime.now(IST).strftime("%H:%M:%S") < "09:21:10":
            time.sleep(5)
    sim = 9 * 60 + 21 if not live else None
    def clock():
        return sim if not live else dt.datetime.now(IST).hour * 60 + dt.datetime.now(IST).minute
    idx1 = feed.index1()
    if not idx1:
        log("no index candles today - market closed; no message"); return
    for x in pm.MONTHLY_EXPIRIES:
        d0 = dt.date.fromisoformat(x); dd = d0
        while True:
            dd += dt.timedelta(days=1)
            if dd.weekday() < 5:
                break
        if dd.isoformat() == today:
            pm.send("Pankaj Sahu style - %s: NO TRADE today. First session after the monthly expiry (%s): he waits for premiums to settle. No alerts today." % (today, x)); return
    exp, spot, oc = pm.chain(tok, cid)
    dte = (dt.date.fromisoformat(exp) - dt.date.fromisoformat(today)).days
    nm = clock()
    seq, t_open, prev_close = feed.index5_closes(nm)
    gap = (t_open - prev_close) if (t_open and prev_close) else 0.0
    gap_big = abs(gap) > GAP_MAX
    side, ker, rising = side_of(seq, t_open, nm)
    if side is None:
        pm.send("Pankaj Sahu style - %s: could not read the Bank Nifty history, so no plan today." % today); return
    near = sorted(oc, key=lambda k: abs(k - idx1["open"][0]))[:25]
    picks, prem, zs, hi = {}, {}, {}, {}
    forced = os.environ.get("FORCE_STRIKES", "").split(",") if os.environ.get("FORCE_STRIKES") else []

    def repick(s, nm_):
        pr = premiums(feed, oc, near, s, nm_, live)
        k = int(forced[0 if s == "CE" else 1]) if forced and len(forced) == 2 and not feed.cache.get("repicked") else K.pick_strike(pr)
        return (k, oc[k][s][0]) if k in oc and s in oc[k] else None
    for s in ("CE", "PE"):
        p = repick(s, nm)
        if p:
            picks[s] = p
    if not picks:
        pm.send("Pankaj Sahu style - %s: I could not read the option prices this morning, so no plan and no alert today." % today); return

    def refresh(nm_):
        for s, (k, sid) in picks.items():
            prev, today_c = feed.opt(sid, refresh=live)
            c = K.closed(today_c, nm_)
            if c and c["mod"]:
                zs[s] = K.zones(K.build_levels(prev, c)); prem[s] = c["close"][-1]; hi[s] = max(c["high"])
            else:
                zs[s], prem[s], hi[s] = [], 0.0, 0.0
    refresh(nm)
    pm.send(plan_msg(today, gap, side, rising, exp, dte, picks, zs, prem, hi, gap_big))
    z0, _t0 = trigger_text(side, picks[side][0], prem[side], zs[side], hi[side]) if side in picks else (None, None)
    pick_prem = dict(prem); last_trigger = ("trig", None if not z0 else round(z0["price"] / 10)); last_trig_nm = nm; trade = None; entries = 0; last_repick = nm; last_side_flip = nm
    while True:
        nm = clock()
        if live:
            time.sleep(max(2.0, 66 - dt.datetime.now(IST).second)); nm = clock()
        else:
            nm += 1
            sim = nm
        if nm > T_HARD or (not live and nm > E_END + 20):
            break
        try:
            refresh(nm)
            if nm % 5 == 0 or not live:
                seq, t_open, prev_close = feed.index5_closes(nm)
                ns, ker, rising = side_of(seq, t_open, nm)
                if ns and ns != side and not trade and nm - last_side_flip >= 10:
                    side = ns; last_side_flip = nm
                    pm.send("MIND CHANGE %s: Loren line has turned - two closed 5-minute candles on the other side. Plan now: %s only. %s" %
                            (hhmm(nm), "CALL" if side == "CE" else "PUT", (lines_block("", picks[side][0], side, prem[side], zs[side]))))
        except RuntimeError as e:
            log(str(e)); continue
        # ---- manage an open trade
        if trade and not trade.get("done"):
            c = K.closed(feed.opt(picks[trade["side"]][1])[1], nm)
            msgs, trade = K.manage(c, trade, nm)
            for m_ in msgs:
                pm.send("[%s %d %s] %s" % (hhmm(nm), trade["strike"], trade["side"], m_))
            if trade.get("done"):
                if trade["done"] == "cost" and entries < 2:
                    trade["reenter"] = True
                else:
                    pm.send("Trade finished (%s). No more alerts today - well done for stopping." % trade["done"])
                    break
            continue
        if nm > (10 * 60 if gap_big else E_END):
            break
        # ---- mind change: market running one way -> new strike
        if not trade and side in picks and prem.get(side) and nm - last_repick >= 10:
            p0, p1 = pick_prem.get(side, 0), prem[side]
            if p1 >= p0 + 120 or p1 <= p0 - 200 or not (450 <= p1 <= 1000):
                np_ = repick(side, nm)
                feed.cache["repicked"] = True
                if np_ and np_[0] != picks[side][0]:
                    old = picks[side][0]; picks[side] = np_; refresh(nm); pick_prem[side] = prem[side]; last_repick = nm; last_trigger = None
                    pm.send("MIND CHANGE %s: the market is running one way (%d %s premium %+.0f since the plan). Do NOT chase it. New strike for the %s side: %d.\n%s\n%s" %
                            (hhmm(nm), old, side, p1 - p0, side, np_[0], lines_block("", np_[0], side, prem[side], zs[side]), NOTE))
        # ---- entries
        if side in picks and (trade is None or trade.get("reenter")):
            k, sid = picks[side]
            c = K.closed(feed.opt(sid)[1], nm)
            lo_m = 9 * 60 + 30 if gap_big else 9 * 60 + 21
            if c and c["mod"] and c["mod"][-1] >= lo_m:
                e = K.pullback_entry(c, zs[side]) or (K.breakout_entry(c, zs[side]) if nm >= 9 * 60 + 25 else None)
                if e and e.get("skip"):
                    key = ("skip", e["zone"]["price"] // 5)
                    if last_trigger != key:
                        last_trigger = key; pm.send("[%s] %d %s closed back above %.0f but %s." % (hhmm(nm), k, side, e["zone"]["price"], e["skip"]))
                elif e and (trade is None or e["zone"]["price"] <= trade["entry"] + 1):
                    tgt = K.next_target(zs[side], e["entry"])
                    trade = dict(entry=e["entry"], stop=e["stop"], t=e["t"], t_end=min(nm + 22, E_END + 5), target=tgt[0], side=side, strike=k, kind=e["kind"])
                    entries += 1
                    pm.send(entry_msg(side, k, e, tgt))
                    continue
        # ---- trigger update (he lowers the trigger as price drifts)
        if side in picks and not trade and prem.get(side):
            z, t = trigger_text(side, picks[side][0], prem[side], zs[side], hi[side])
            key = ("trig", None if not z else round(z["price"] / 10))
            if t and key != last_trigger and nm - last_trig_nm >= 4:
                last_trigger = key; last_trig_nm = nm; pm.send("[%s] %s Premium now %.0f." % (hhmm(nm), t, prem[side]))
    if not trade:
        pm.send("Pankaj Sahu style - %s is over. No clean entry came, so no trade today. A no-trade day is also a good day. (%s)" % (today, "big gap day" if gap_big else "pullback never closed back above a zone"))
    elif not trade.get("done"):
        pm.send("Watch ended before the trade finished - please manage the exit yourself now.")


if __name__ == "__main__":
    try:
        main()
    except (Exception, SystemExit) as e:
        log("FAILED: %s" % e)
        try:
            if not (isinstance(e, SystemExit) and e.code in (0, None)):
                pm.send("Pankaj Sahu style: the morning tool could NOT run (%s). Please do not rely on this channel today." % str(e)[:100])
        finally:
            raise
