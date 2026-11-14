"""Draw an option strike's 1-minute chart with the marked lines (yesterday O/H/L/C, reversal points, zones) -> PNG bytes.  matplotlib only."""
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def hhmm(m):
    return "%02d:%02d" % (m // 60, m % 60)


def draw(title, prev, today, levels, zones, prem):
    """prev/today = 1-min candle dicts (start-minute stamps, today CLOSED candles only); levels = [(price, type, label)]; zones = [{price,strength,names}]."""
    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=110)
    xs, o, h, l, c, lab = [], [], [], [], [], []
    i = 0
    if prev and prev["mod"]:                                   # last 45 minutes of yesterday, for the 'where it closed' context
        n = len(prev["mod"]); st = max(0, n - 25)
        for k in range(st, n):
            xs.append(i); o.append(prev["open"][k]); h.append(prev["high"][k]); l.append(prev["low"][k]); c.append(prev["close"][k]); lab.append(None); i += 1
    sep = i
    for k in range(len(today["mod"])):
        xs.append(i); o.append(today["open"][k]); h.append(today["high"][k]); l.append(today["low"][k]); c.append(today["close"][k]); lab.append(today["mod"][k]); i += 1
    for x, oo, hh, ll, cc in zip(xs, o, h, l, c):
        col = "#2a9d5c" if cc >= oo else "#d64545"
        ax.plot([x, x], [ll, hh], color=col, lw=0.9, zorder=3)
        ax.add_patch(plt.Rectangle((x - 0.32, min(oo, cc)), 0.64, max(abs(cc - oo), 0.4), color=col, zorder=4))
    xmax = (xs[-1] if xs else 0) + 14
    near = [v for v, t, _ in levels if t != "round" and abs(v - prem) <= 130]
    allp = h + l + near
    ylo, yhi = min(allp) - 10, max(allp) + 10
    ax.set_ylim(ylo, yhi)
    levels = [x for x in levels if ylo <= x[0] <= yhi]
    zones = [z for z in zones if ylo <= z["price"] <= yhi]
    shade = {"prev": ("#1f5fbf", "-", 1.3), "s5": ("#8a8a8a", ":", 0.9), "s15": ("#8a8a8a", "--", 1.0), "t5": ("#8a8a8a", ":", 0.9),
             "open": ("#b8860b", "-", 1.2), "dayhl": ("#b8860b", "--", 1.0), "mid": ("#7a3fa0", "--", 0.9), "round": ("#bbbbbb", ":", 0.6)}
    drawn = []
    for v, t, lbl in levels:
        if t == "round":
            continue
        col, ls, lw = shade.get(t, ("#888", ":", 0.8))
        ax.axhline(v, color=col, ls=ls, lw=lw, zorder=1, alpha=0.9)
        if t in ("prev", "open", "dayhl", "mid") and not any(abs(v - d) < 6 for d in drawn):
            ax.text(xmax - 0.5, v, " %s %.0f" % (lbl, v), va="center", ha="left", fontsize=7.5, color=col)
            drawn.append(v)
    for z in zones:
        if z["strength"] >= 3:
            ax.axhspan(z["lo"] - 1, z["hi"] + 1 if z["hi"] > z["lo"] else z["lo"] + 3, color="#f4b400", alpha=0.22, zorder=0)
            ax.text(1, z["price"], "STRONG %.0f" % z["price"], va="bottom", fontsize=7.5, color="#8a6400", weight="bold")
    if sep:
        ax.axvline(sep - 0.5, color="#555", lw=0.8)
        ax.text(sep - 0.5, yhi, " today", va="top", fontsize=8, color="#555")
    ax.set_xlim(-1, xmax + 12)
    ticks = [x for x, m in zip(xs, lab) if m is not None and m % 5 == 0]
    ax.set_xticks(ticks[::2]); ax.set_xticklabels([hhmm(m) for m in [t for t in lab if t is not None and t % 5 == 0]][::2], fontsize=7)
    ax.set_title(title, fontsize=11, loc="left")
    ax.grid(axis="y", alpha=0.12)
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png"); plt.close(fig)
    return buf.getvalue()
