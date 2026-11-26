"""SHADOW scoring of the open-interest-flow angle (O4). Observation only: it NEVER sends a Telegram message and never
changes an entry, stop or target. It writes one 'SHADOW|{json}' line to the run log at every ENTRY so that real forward days can be
scored later with and without the filter (pre-registered in research/loren/PREREG_oi_angle_2026-11-18.md).

O4 (live approximation): sum over all strikes of (PE oi - PE previous_oi) minus (CE oi - CE previous_oi), taken from the Dhan option chain
at the entry minute; 'aligned' = positive for a CALL (CE) trade, negative for a PUT trade. Backtest used the change since 09:16 from OI Pulse;
the prior-close baseline agrees with it on 386/392 historical trades (98.5%), so it is an approximation, not an identical feature.
Any failure here is swallowed: the alert path must never depend on this module."""
import datetime as dt
import json

import pankaj_morning_alert as pm


def _oi_delta(tok, cid):
    exps = pm.post("/optionchain/expirylist", {"UnderlyingScrip": pm.BN_ID, "UnderlyingSeg": "IDX_I"}, tok, cid, "chain").get("data") or []
    today = pm.AS_OF or dt.datetime.now(pm.IST).strftime("%Y-%m-%d")
    exps = [e for e in exps if e >= today]
    oc = pm.post("/optionchain", {"UnderlyingScrip": pm.BN_ID, "UnderlyingSeg": "IDX_I", "Expiry": exps[0]}, tok, cid, "chain").get("data") or {}
    d = {"CE": 0.0, "PE": 0.0}; n = 0
    for v in (oc.get("oc") or {}).values():
        for s in ("ce", "pe"):
            x = v.get(s) or {}
            if x.get("oi") is not None and x.get("previous_oi") is not None:
                d[s.upper()] += float(x["oi"]) - float(x["previous_oi"]); n += 1
    return exps[0], float(oc.get("last_price") or 0), d, n


def log_entry(tok, cid, kind, side, strike, entry_prem, minute):
    """Call right after an ENTRY message. Safe: returns None on any error."""
    if pm.AS_OF:                                             # replays of past days have no historical chain: log nothing
        return None
    try:
        exp, spot, d, n = _oi_delta(tok, cid)
        net = d["PE"] - d["CE"]
        aligned = (net > 0) == (side == "CE")
        rec = dict(date=dt.datetime.now(pm.IST).strftime("%Y-%m-%d"), minute=minute, kind=str(kind)[:40], side=side, strike=strike,
                   entry_prem=round(float(entry_prem), 2), spot=spot, expiry=exp, ce_delta=d["CE"], pe_delta=d["PE"], strikes_read=n,
                   o4_aligned=bool(aligned))
        pm.log("SHADOW|" + json.dumps(rec))
        return rec
    except (Exception, SystemExit) as e:                      # noqa: BLE001 - post() raises SystemExit on a rejected token - observation must never break the alert
        try:
            pm.log("SHADOW|error|%s" % str(e)[:120])
        except Exception:                                    # noqa: BLE001
            pass
        return None


_last_snap = {"m": None}


def snapshot_strikes(tok, cid, minute, width=1000):
    """Every 5th minute during the live watch: log per-strike OI (+ change since previous close, last price, volume) within +-width of spot.
    Log-only ('STRIKES|{json}'); one extra option-chain request; every error swallowed. Purpose: forward test of the intraday strike-level
    'who is behind the OI change' idea that the historical warehouse cannot test (it only has settled end-of-day strike data)."""
    if pm.AS_OF or minute % 5 != 0 or _last_snap["m"] == minute:
        return None
    _last_snap["m"] = minute
    try:
        exps = pm.post("/optionchain/expirylist", {"UnderlyingScrip": pm.BN_ID, "UnderlyingSeg": "IDX_I"}, tok, cid, "chain").get("data") or []
        today = dt.datetime.now(pm.IST).strftime("%Y-%m-%d")
        exps = [e for e in exps if e >= today]
        oc = pm.post("/optionchain", {"UnderlyingScrip": pm.BN_ID, "UnderlyingSeg": "IDX_I", "Expiry": exps[0]}, tok, cid, "chain").get("data") or {}
        spot = float(oc.get("last_price") or 0)
        rows = []
        for k, v in (oc.get("oc") or {}).items():
            ks = int(float(k))
            if abs(ks - spot) > width:
                continue
            for s in ("ce", "pe"):
                x = v.get(s) or {}
                if x.get("oi") is None:
                    continue
                rows.append([ks, s, x.get("oi"), x.get("previous_oi"), x.get("last_price"), x.get("previous_close_price"), x.get("volume"), x.get("top_bid_price"), x.get("top_ask_price")])
        pm.log("STRIKES|" + json.dumps(dict(date=today, minute=minute, spot=spot, expiry=exps[0], cols="strike,type,oi,prev_oi,ltp,prev_close,volume,bid,ask", rows=rows), separators=(",", ":")))
        return len(rows)
    except (Exception, SystemExit) as e:                      # noqa: BLE001 - post() raises SystemExit on a rejected token
        try:
            pm.log("STRIKES|error|%s" % str(e)[:120])
        except Exception:                                    # noqa: BLE001
            pass
        return None


# ------------------------------------------------------------------------------------------------------------------ PAPER TRADING
# One lot, SIMULATED only. No broker order is ever created here. Assumptions (all labelled in the message): fill 2 points worse than the
# signal price on entry and on exit, brokerage Rs 40 per round trip, ~0.15% of exit turnover for taxes and exchange charges.
import os
PAPER_LOT = int(os.environ.get("PAPER_LOT", "30"))          # BANKNIFTY lot size: VERIFY against the current NSE circular before trusting rupee figures
PAPER_SLIP = 2.0
_paper = {"open": None, "day_pts": 0.0, "day_rs": 0.0, "n": 0}


def paper_enter(side, strike, entry, minute, kind):
    try:
        # v2 = "early, no re-entry" rule (owner yes 2026-11-20; found on past data, net of costs: no trap re-entries, entry before 09:30). Same trade, just labelled.
        v2 = (not str(kind).upper().startswith("TRAP")) and minute < 9 * 60 + 30
        _paper["open"] = dict(side=side, strike=strike, entry=float(entry), minute=minute, kind=str(kind)[:40], v2=bool(v2))
        pm.log("PAPER|enter|" + json.dumps(_paper["open"]))
    except (Exception, SystemExit):                          # noqa: BLE001
        pass


def paper_exit(price, minute, reason):
    """Close the open paper trade at `price` (the tool's own exit level). Sends ONE labelled Telegram line."""
    try:
        t = _paper["open"]
        if not t or price is None:
            return
        _paper["open"] = None
        gross = float(price) - t["entry"]
        pts = gross - 2 * PAPER_SLIP
        rs = pts * PAPER_LOT - 40.0 - 0.0015 * float(price) * PAPER_LOT
        _paper["day_pts"] += pts; _paper["day_rs"] += rs; _paper["n"] += 1
        rec = dict(t, exit=round(float(price), 2), exit_minute=minute, reason=reason, gross_pts=round(gross, 1), net_pts=round(pts, 1), net_rs=round(rs), lot=PAPER_LOT)
        pm.log("PAPER|exit|" + json.dumps(rec))
        pm.send("PAPER TRADE (practice only, no real order) - %d %s: in %.0f, out %.0f (%s). %+.0f points = about Rs %+.0f for 1 lot (%d units) after assumed costs and slippage. Today so far: %d paper trade(s), Rs %+.0f. Early-no-re-entry rule: %s."
                % (t["strike"], t["side"], t["entry"], float(price), reason, pts, rs, PAPER_LOT, _paper["n"], _paper["day_rs"], "YES" if t.get("v2") else "no"))
    except (Exception, SystemExit):                          # noqa: BLE001
        pass


# ------------------------------------------------------------------------------------------------------------------ RT-B SHADOW (log only)
# RT-B = "second touch only": a pullback entry counts only if the same zone was already touched from above at least 3 candles before the touch
# candle; breakout entries do not count. Pre-registered test: passed 2026 blocks A/B/C, FAILED block D, so NOT supported. This only LABELS each
# live entry ('RTB|{json}') so forward days can be scored with and without it. It never sends, never changes an entry/stop/target.
# Limit: it labels entries the live tool actually took; it does not simulate extra entries RT-B alone might have taken.
def rtb_label(c, e, side, strike, minute):
    try:
        if pm.AS_OF:
            return None
        import pankaj_core as K
        ok = False
        if e and e.get("kind") == "pullback" and e.get("touch_t") is not None:
            tj = c["mod"].index(e["touch_t"])
            z = e["zone"]
            ok = any(K.touch(c, k, [z], max(c["high"][:k + 1])) for k in range(1, max(1, tj - 2)))
        rec = dict(date=dt.datetime.now(pm.IST).strftime("%Y-%m-%d"), minute=minute, side=side, strike=strike, kind=str((e or {}).get("kind"))[:40], rtb_ok=bool(ok))
        pm.log("RTB|" + json.dumps(rec))
        return rec
    except (Exception, SystemExit) as ex:                     # noqa: BLE001
        try:
            pm.log("RTB|error|%s" % str(ex)[:120])
        except Exception:                                    # noqa: BLE001
            pass
        return None


# ------------------------------------------------------------------------------------------------------------------ W SYSTEM PAPER (log only)
# Owner yes 2026-11-26. Our own 'writers' system, PAPER ONLY: no order is ever placed. Purpose: measure the REAL cost of a trade (bid/ask spread)
# because past data has no bid/ask (research: W system earns +4..+7 points/trade before costs in every period; profitable only if the real
# cost is <= ~4.4 points). Rule (best setting of the 2026-11-24 grid): writers' flow w = [(PE OI - PE OI at first reading) - (CE OI - CE OI at
# first reading)] / total OI at first reading; signal when |w| >= 2.0% AND the index moved >= 80 points the same way over the last 10 minutes;
# buy the side's strike whose premium is nearest 615 (545-715); exit on a 30-point stop, when the writers weaken (w below half the threshold,
# same direction), or at the end of the watch. Max 2 trades a day. Every minute logs 'WSYS|tick'; entries/exits log ltp AND bid/ask so the
# result can be scored both at the chart price and at the real buy-at-ask / sell-at-bid price. All errors are swallowed.
W_TH, W_MOVE, W_PREM, W_STOP = 0.020, 80.0, 615.0, 30.0
_w = {"first": None, "spot": {}, "pos": None, "n": 0, "last_m": None}


def _chain(tok, cid):
    exps = pm.post("/optionchain/expirylist", {"UnderlyingScrip": pm.BN_ID, "UnderlyingSeg": "IDX_I"}, tok, cid, "chain").get("data") or []
    today = dt.datetime.now(pm.IST).strftime("%Y-%m-%d")
    exps = [e for e in exps if e >= today]
    oc = pm.post("/optionchain", {"UnderlyingScrip": pm.BN_ID, "UnderlyingSeg": "IDX_I", "Expiry": exps[0]}, tok, cid, "chain").get("data") or {}
    return exps[0], oc


def w_tick(tok, cid, minute):
    if pm.AS_OF or _w["last_m"] == minute:
        return None
    _w["last_m"] = minute
    try:
        exp, oc = _chain(tok, cid)
        spot = float(oc.get("last_price") or 0)
        ce = pe = 0.0
        book = {}
        for k, v in (oc.get("oc") or {}).items():
            for s in ("ce", "pe"):
                x = v.get(s) or {}
                if x.get("oi") is None:
                    continue
                if s == "ce":
                    ce += float(x["oi"])
                else:
                    pe += float(x["oi"])
                book[(int(float(k)), s.upper())] = (x.get("last_price"), x.get("top_bid_price"), x.get("top_ask_price"))
        if _w["first"] is None:
            _w["first"] = (ce, pe)
        ce0, pe0 = _w["first"]
        tot = ce0 + pe0
        w = ((pe - pe0) - (ce - ce0)) / tot if tot else 0.0
        _w["spot"][minute] = spot
        old = _w["spot"].get(minute - 10)
        move = (spot - old) if old else None
        rec = dict(m=minute, w=round(w, 5), spot=spot, move10=None if move is None else round(move, 1))
        p = _w["pos"]
        if p:
            ltp, bid, ask = book.get((p["k"], p["side"]), (None, None, None))
            sg = 1 if p["side"] == "CE" else -1
            reason = None
            if ltp is not None and float(ltp) <= p["ltp"] - W_STOP:
                reason = "stop"
            elif sg * w < 0.5 * W_TH:
                reason = "writers weakened"
            if reason:
                _w_exit(minute, ltp, bid, reason)
        elif _w["n"] < 2 and abs(w) >= W_TH and move is not None and (1 if w > 0 else -1) * move >= W_MOVE and minute <= 10 * 60 + 30:
            side = "CE" if w > 0 else "PE"
            c = [(k, v) for (k, sd), v in book.items() if sd == side and v[0] and 545 <= float(v[0]) <= 715]
            if c:
                k, (ltp, bid, ask) = min(c, key=lambda t: abs(float(t[1][0]) - W_PREM))
                _w["pos"] = dict(side=side, k=k, m=minute, ltp=float(ltp), bid=bid, ask=ask, w=round(w, 5), move10=round(move, 1), expiry=exp)
                _w["n"] += 1
                pm.log("WSYS|enter|" + json.dumps(_w["pos"]))
        pm.log("WSYS|tick|" + json.dumps(rec))
        return rec
    except (Exception, SystemExit) as e:                      # noqa: BLE001
        try:
            pm.log("WSYS|error|%s" % str(e)[:120])
        except Exception:                                    # noqa: BLE001
            pass
        return None


def _w_exit(minute, ltp, bid, reason):
    p = _w["pos"]
    _w["pos"] = None
    gross = (float(ltp) - p["ltp"]) if ltp is not None else None
    real = (float(bid) - float(p["ask"])) if (bid and p.get("ask")) else None
    rec = dict(p, exit_m=minute, exit_ltp=ltp, exit_bid=bid, reason=reason, gross_pts_ltp=None if gross is None else round(gross, 2),
               pts_at_real_bid_ask=None if real is None else round(real, 2), spread_cost=None if (gross is None or real is None) else round(gross - real, 2))
    pm.log("WSYS|exit|" + json.dumps(rec))
    try:
        pm.send("W-SYSTEM PAPER (practice only, no order) - %d %s: in %.0f, out %s (%s). Chart-price result %s pts; at real buy-ask / sell-bid %s pts (spread cost %s)."
                % (p["k"], p["side"], p["ltp"], ltp, reason, rec["gross_pts_ltp"], rec["pts_at_real_bid_ask"], rec["spread_cost"]))
    except Exception:                                        # noqa: BLE001
        pass


def w_finish(tok, cid, clock, end_minute):
    """After the main watch ends: keep ticking the W paper system until end_minute, then close any open paper trade. Never raises."""
    if pm.AS_OF:
        return
    try:
        import time
        while True:
            nm = clock()
            if nm > end_minute:
                break
            w_tick(tok, cid, nm)
            time.sleep(max(2.0, 66 - dt.datetime.now(pm.IST).second))
        if _w["pos"]:
            exp, oc = _chain(tok, cid)
            p = _w["pos"]
            x = {}
            for k, v in (oc.get("oc") or {}).items():
                if int(float(k)) == p["k"]:
                    x = v.get(p["side"].lower()) or {}
            _w_exit(clock(), x.get("last_price"), x.get("top_bid_price"), "watch ended")
    except (Exception, SystemExit) as e:                      # noqa: BLE001
        try:
            pm.log("WSYS|error|finish %s" % str(e)[:120])
        except Exception:                                    # noqa: BLE001
            pass
