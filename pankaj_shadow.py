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
                rows.append([ks, s, x.get("oi"), x.get("previous_oi"), x.get("last_price"), x.get("previous_close_price"), x.get("volume")])
        pm.log("STRIKES|" + json.dumps(dict(date=today, minute=minute, spot=spot, expiry=exps[0], cols="strike,type,oi,prev_oi,ltp,prev_close,volume", rows=rows), separators=(",", ":")))
        return len(rows)
    except (Exception, SystemExit) as e:                      # noqa: BLE001 - post() raises SystemExit on a rejected token
        try:
            pm.log("STRIKES|error|%s" % str(e)[:120])
        except Exception:                                    # noqa: BLE001
            pass
        return None
