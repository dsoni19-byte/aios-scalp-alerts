"""Pure logic (no network) of the Pankaj Sahu style line-marking + premium-alert tool.  All candles are dicts
{mod, open, high, low, close} with START-minute stamps (09:16 = 556).  A candle that starts at minute s is CLOSED at minute s+1.
His method as read from his live mornings (research/PANKAJ_LIVE_VIDEO_NOTES_2026-11-14.md): lines on the OPTION chart from yesterday's
close/low/high/open, 5- and 15-minute swings, today's open/swings, round numbers, 50% of the first 5-minute candle; a zone is strong when
3-4 of them sit together; he buys a pullback into a zone only when a 1-minute candle CLOSES back above it; stop under the zone; cost-to-cost;
trail; 15-20 minutes.  Not proven: the earlier replay tests of mechanical versions found no edge.  Hand-drawn trend lines are NOT modelled."""

OPEN_M = 555
CLUSTER = 7.0          # lines this close (premium points) are one zone
NEAR, PULL, UNDER = 5.0, 15.0, 10.0
RISK_MIN, RISK_MAX = 15.0, 45.0
COST_AT, TRAIL_AT = 25.0, 40.0


def closed(c, nm):
    """Candles fully closed at clock minute nm (start <= nm-1)."""
    if not c:
        return None
    n = sum(1 for m in c["mod"] if m <= nm - 1)
    return {k: v[:n] for k, v in c.items()}


def bars(c, size, nm=None):
    """Complete `size`-minute bars from 1-minute candles -> list of (start, high, low, close)."""
    o = {}
    for i, m in enumerate(c["mod"]):
        b = (m - OPEN_M) // size
        h, l, cl, last = o.get(b, (-1e18, 1e18, 0, 0))
        o[b] = (max(h, c["high"][i]), min(l, c["low"][i]), c["close"][i], m)
    out = []
    for b in sorted(o):
        h, l, cl, last = o[b]
        if last < OPEN_M + b * size + size - 1:
            continue                          # still forming (only checked when a clock minute is given)
        out.append((OPEN_M + b * size, h, l, cl))
    return out


def swings(bs, wing):
    r = []
    for i in range(wing, len(bs) - wing):
        if bs[i][1] == max(x[1] for x in bs[i - wing:i + wing + 1]):
            r.append((bs[i][1], "high"))
        if bs[i][2] == min(x[2] for x in bs[i - wing:i + wing + 1]):
            r.append((bs[i][2], "low"))
    return r


def build_levels(prev, c):
    """prev = yesterday's 1-min candles of this option (or None); c = today's CLOSED candles.  -> list of (price, type, label)."""
    L = []
    if prev and prev["mod"]:
        L += [(prev["open"][0], "prev", "yesterday open"), (max(prev["high"]), "prev", "yesterday high"),
              (min(prev["low"]), "prev", "yesterday low"), (prev["close"][-1], "prev", "yesterday close")]
        for v, k in swings(bars(prev, 5), 2):
            L.append((v, "s5", "5-min swing %s" % k))
        for v, k in swings(bars(prev, 15), 1):
            L.append((v, "s15", "15-min swing %s" % k))
    if c and c["mod"]:
        L.append((c["open"][0], "open", "today open"))
        L.append((max(c["high"]), "dayhl", "today high"))
        L.append((min(c["low"]), "dayhl", "today low"))
        for v, k in swings(bars(c, 5), 1):
            L.append((v, "t5", "today 5-min swing %s" % k))
        f = [i for i, m in enumerate(c["mod"]) if m < OPEN_M + 5]
        if f and c["mod"][-1] >= OPEN_M + 4:
            hi = max(c["high"][i] for i in f); lo = min(c["low"][i] for i in f)
            L.append(((hi + lo) / 2.0, "mid", "50% of first 5-min candle"))
    lo = min([x[0] for x in L] or [0]); hi = max([x[0] for x in L] or [0])
    k = int(lo // 50) * 50
    while k <= hi + 50:                       # round numbers (he calls 800 / 650 "psychological" zones)
        if lo - 50 <= k <= hi + 50 and k > 0:
            L.append((float(k), "round", "round number %d" % k))
        k += 50
    return L


def zones(levels, min_strength=2):
    """Merge lines within CLUSTER points -> [{price, strength, names}] sorted by price.  strength = number of different line TYPES together."""
    ls = sorted(levels)
    out, cur = [], []
    for x in ls:
        if cur and x[0] - cur[-1][0] > CLUSTER:
            out.append(cur); cur = []
        cur.append(x)
    if cur:
        out.append(cur)
    zs = []
    for g in out:
        types = {x[1] for x in g}
        zs.append(dict(price=sum(x[0] for x in g) / len(g), lo=g[0][0], hi=g[-1][0], strength=len(types), names=sorted({x[2] for x in g})))
    return [z for z in zs if z["strength"] >= min_strength]


def zone_text(z):
    return "%.0f%s (%s)" % (z["price"], " STRONG" if z["strength"] >= 3 else "", ", ".join(z["names"][:4]))


def touch(c, j, zs, hi_so_far):
    """Candle j touched a zone from above (pullback): low within NEAR points of it, closed not UNDER below it, zone >= PULL under the day's high."""
    hit = [z for z in zs if c["low"][j] <= z["price"] + NEAR and c["close"][j] >= z["price"] - UNDER and z["price"] <= hi_so_far - PULL]
    return max(hit, key=lambda z: z["price"]) if hit else None


def pullback_entry(c, zs):
    """Latest closed candle i confirms a pullback entry: a touch within the last 4 candles, then a green candle closing above the zone
    (and, if later than the touch, at or above the previous candle's high).  -> dict or None.  Never looks past the last closed candle."""
    n = len(c["mod"])
    if n < 3:
        return None
    i = n - 1
    for j in range(max(1, i - 3), i + 1):
        if c["mod"][i] - c["mod"][j] != i - j:
            continue
        z = touch(c, j, zs, max(c["high"][:j + 1]))
        if not z:
            continue
        if c["close"][i] > c["open"][i] and c["close"][i] > z["price"] and (i == j or c["close"][i] >= c["high"][i - 1]):
            E = c["close"][i]; stop = min(c["low"][j:i + 1]) - 2.0
            if E - stop > RISK_MAX:
                return dict(skip="stop would be %.0f points away (more than %d) - too wide, skip" % (E - stop, RISK_MAX), zone=z)
            if E - stop < RISK_MIN:
                stop = E - RISK_MIN
            return dict(kind="pullback", zone=z, entry=E, stop=stop, t=c["mod"][i], touch_t=c["mod"][j])
    return None


def breakout_entry(c, zs):
    """His second plan: no pullback came; a green candle closes above a strong zone that held the last 3 closes below it."""
    n = len(c["mod"])
    if n < 5:
        return None
    i = n - 1
    if c["close"][i] <= c["open"][i]:
        return None
    for z in zs:
        if z["strength"] >= 2 and all(c["close"][i - k] <= z["price"] for k in (1, 2, 3)) and c["close"][i] > z["price"] and c["close"][i] - z["price"] <= 20:
            E = c["close"][i]; stop = min(c["low"][i - 2:i + 1]) - 2.0
            stop = min(stop, E - 25.0)
            if E - stop > RISK_MAX:
                continue
            return dict(kind="breakout", zone=z, entry=E, stop=stop, t=c["mod"][i])
    return None


def next_target(zs, E):
    up = [z for z in zs if z["price"] >= E + 28]
    if up:
        z = min(up, key=lambda z: z["price"])
        return min(z["price"], E + 60), z
    return E + 35, None


def manage(c, tr, nm):
    """Advance an open trade over closed candles after entry.  tr = dict(entry, stop0, stop, t, state...).  Returns (messages, tr).
    Stop is checked on closed candles' lows (once a minute) - a real stop order at the broker is better than this."""
    msgs = []
    E = tr["entry"]
    for i, m in enumerate(c["mod"]):
        if m <= tr["t"] or m <= tr.get("seen", -1):
            continue
        tr["seen"] = m
        if c["low"][i] <= tr["stop"]:
            tr["done"] = "cost" if abs(tr["stop"] - E) < 1e-9 else ("trail" if tr["stop"] > E else "stop")
            tr["exit_price"] = tr["stop"]
            msgs.append("EXIT now - premium touched your stop %.0f (%+.0f points from entry) at %s." % (tr["stop"], tr["stop"] - E, hhmm(m)))
            return msgs, tr
        tr["best"] = max(tr.get("best", E), c["high"][i])
        if tr["best"] >= E + COST_AT and tr["stop"] < E and not tr.get("cost_sent"):
            tr["stop"] = E; tr["cost_sent"] = True
            msgs.append("PREMIUM reached %.0f (+%.0f). Move your stop to your entry price %.0f - now you cannot lose on this trade." % (tr["best"], tr["best"] - E, E))
        if tr["best"] >= tr["target"] and not tr.get("target_sent"):
            tr["target_sent"] = True
            msgs.append("FIRST TARGET %.0f reached (%+.0f). Book part or all. If you stay, keep raising the stop under each closed 1-minute candle." % (tr["target"], tr["target"] - E))
        if tr["best"] >= E + TRAIL_AT:
            ns = c["low"][i] - 2.0
            if ns >= tr["stop"] + 5.0 and ns < c["close"][i]:
                tr["stop"] = ns
                msgs.append("TRAIL: raise your stop to %.0f (2 points under the last candle's low). Locked in %+.0f." % (ns, ns - E))
    if nm >= tr["t_end"] and not tr.get("done"):
        tr["done"] = "time"
        msgs.append("TIME is up (15-20 minutes is his window). Book the trade now unless you have a reason - last close %.0f (%+.0f points)." % (c["close"][-1], c["close"][-1] - E))
    return msgs, tr


def hhmm(m):
    return "%02d:%02d" % (m // 60, m % 60)


# ------------------------------------------------------------------------------------------------------------------ side (Loren) + strike
def kernel(closes, h=8, r=8, x0=25):
    """Rational-quadratic kernel line of the Loren indicator (replica, matched the owner's chart to 0.16 point).  None until 27 closes."""
    w = [(1 + (i * i) / (2.0 * h * h * r)) ** (-r) for i in range(x0 + 2)]
    sw = sum(w); out = [None] * len(closes)
    for t in range(len(w) - 1, len(closes)):
        out[t] = sum(closes[t - i] * w[i] for i in range(len(w))) / sw
    return out


def pick_strike(prem, target=700.0, lo=600.0, hi=850.0):
    ok = {k: v for k, v in prem.items() if v and v > 0}
    if not ok:
        return None
    band = {k: v for k, v in ok.items() if lo <= v <= hi}
    src = band or ok
    return min(src, key=lambda k: abs(src[k] - target))
