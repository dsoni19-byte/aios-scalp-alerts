"""Weekly PAPER-trade tally, read-only: reads 'PAPER|exit|{json}' and 'SHADOW|{json}' lines from the pankaj-morning run logs and sends ONE
Telegram summary. Verdict bar (fixed 2026-11-19): >=30 paper trades, total net points > 0 AND total excluding the best 3 > 0, else FAIL.
Never places or changes anything. DRY_RUN=1 prints instead of sending."""
import json
import os
import re
import subprocess
import sys

import requests

REPO = os.environ.get("GITHUB_REPOSITORY", "dsoni19-byte/aios-scalp-alerts")
SINCE = os.environ.get("SINCE", "2026-11-20")
DRY = os.environ.get("DRY_RUN", "0") == "1"


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, timeout=300).stdout


def main():
    runs = json.loads(sh("gh", "run", "list", "--repo", REPO, "--workflow", "pankaj-morning.yml", "--limit", "100", "--json", "databaseId,createdAt,conclusion,event"))
    runs = [r for r in runs if r["createdAt"][:10] >= SINCE and r["event"] == "workflow_dispatch"]
    paper, shadow, fails, errs = {}, [], 0, 0
    wsys = {}
    for r in runs:
        log = sh("gh", "run", "view", str(r["databaseId"]), "--repo", REPO, "--log")
        if r.get("conclusion") not in ("success", None, ""):
            fails += 1
        for line in log.splitlines():
            m = re.search(r"PAPER\|exit\|(\{.*\})", line)
            if m:
                try:
                    j = json.loads(m.group(1)); paper[(r["databaseId"], j["minute"], j["side"], j["strike"], j["exit_minute"])] = (r["createdAt"], j, r["databaseId"])
                except ValueError:
                    pass
            m = re.search(r"SHADOW\|(\{.*\})", line)
            if m:
                try:
                    shadow.append((r["databaseId"], json.loads(m.group(1))))
                except ValueError:
                    pass
            m = re.search(r"WSYS\|exit\|(\{.*\})", line)
            if m:
                try:
                    j = json.loads(m.group(1)); wsys[(r["databaseId"], j["m"], j["side"], j["k"])] = j
                except ValueError:
                    pass
            if re.search(r"(SHADOW|STRIKES|WSYS)\|error", line):
                errs += 1
    tr = sorted(paper.values(), key=lambda x: (x[0], x[1]["minute"]))
    pts = [j["net_pts"] for _, j, _r in tr]; n = len(pts)
    sm = {(rid, s["minute"], s["side"], s["strike"]): s["o4_aligned"] for rid, s in shadow}
    head = "PAPER REPORT (practice only, no real orders) since %s: %d run(s)%s." % (SINCE, len(runs), (", %d failed" % fails) if fails else "")
    if n == 0:
        msg = head + " No paper trades yet (no entry signals so far). Too early: 0 of 30."
    else:
        tot = sum(pts); ex3 = sum(sorted(pts, reverse=True)[3:])
        peak = run = dd = 0.0
        for x in pts:
            run += x; peak = max(peak, run); dd = min(dd, run - peak)
        wins = sum(1 for x in pts if x > 0)
        al = [j["net_pts"] for _c, j, rid in tr if sm.get((rid, j["minute"], j["side"], j["strike"]))]
        msg = "%s Trades: %d of 30 needed. Total %+.0f points (about Rs %+.0f for 1 lot, lot size unverified), average %+.1f, wins %d of %d, total without the best 3: %+.0f, worst losing stretch %+.0f." % (
            head, n, tot, sum(j["net_rs"] for _c, j, _r in tr), tot / n, wins, n, ex3, dd)
        if al:
            msg += " Open-interest agreed on %d trade(s), average %+.1f." % (len(al), sum(al) / len(al))
        if n < 30:
            msg += " VERDICT: too early - no conclusion yet."
        else:
            msg += " VERDICT: %s." % ("PASS - bar met, still a small sample" if tot > 0 and ex3 > 0 else "FAIL - do not trade real money with this")
    v2 = [j["net_pts"] for _c, j, _r in tr if j.get("v2")]
    if v2:
        t2 = sum(v2); e2 = sum(sorted(v2, reverse=True)[3:])
        msg += " EARLY-NO-RE-ENTRY version (entry before 9:30, no re-entry after a stop): %d of 30 trades, total %+.0f points, average %+.1f, without best 3: %+.0f%s." % (
            len(v2), t2, t2 / len(v2), e2, "" if len(v2) < 30 else (" - PASS" if t2 > 0 and e2 > 0 else " - FAIL"))
    W = list(wsys.values())
    if W:
        g = [x["gross_pts_ltp"] for x in W if x.get("gross_pts_ltp") is not None]
        rl = [x["pts_at_real_bid_ask"] for x in W if x.get("pts_at_real_bid_ask") is not None]
        sp = [x["spread_cost"] for x in W if x.get("spread_cost") is not None]
        cost = (sum(sp) / len(sp) + 1.9) if sp else None
        msg += " W-SYSTEM PAPER: %d of 20 trades; chart-price total %+.0f; at real bid/ask %+.0f; average spread cost %s, all-in cost per trade about %s points (needs <= 4.4)%s." % (
            len(W), sum(g), sum(rl), ("%.1f" % (sum(sp) / len(sp))) if sp else "n/a", ("%.1f" % cost) if cost is not None else "n/a",
            "" if len(W) < 20 or cost is None else (" - COST OK, worth considering" if cost <= 4.4 else " - COST TOO HIGH, drop the W system"))
    if errs:
        msg += " Note: %d logging error line(s) found." % errs
    print(msg)
    if DRY:
        return
    j = requests.post("https://api.telegram.org/bot%s/sendMessage" % os.environ["TELEGRAM_BOT_TOKEN"].strip(), timeout=20,
                      json={"chat_id": os.environ["PANKAJ_CHAT_ID"].strip(), "text": msg[:4000]}).json()
    if not j.get("ok"):
        sys.exit("telegram failed")


if __name__ == "__main__":
    main()
