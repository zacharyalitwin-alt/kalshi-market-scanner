"""
analyze.py - which Kalshi markets does the crowd price well, and which does it not?

Reads data/snapshots/*.csv and data/results.csv (from record.py) and reports:
  1. Calibration: when the market said X%, how often did YES actually happen?
     (at 24 h, 6 h, 1 h and 15 min before close)
  2. Accuracy by category (Brier score and bias), 1 h before close.
  3. Trading test: buy YES or NO at the ask in a price range, hold to settlement,
     Kalshi taker fee included. Rules are chosen on the first 2/3 of events (TRAIN)
     and judged on the last 1/3 (TEST), which they never saw.

    python analyze.py
    python analyze.py --since "2026-11-10"      # only markets that closed after this date
    python analyze.py --last 72                 # only markets that closed in the last 72 h

Research only. Places no orders.
"""
import argparse, csv, glob, math, os, statistics, sys, time
from collections import defaultdict
from datetime import datetime

import common as C

HORIZONS = [("24h", 24 * 3600), ("6h", 6 * 3600), ("1h", 3600), ("15m", 900)]
WINDOW = (0.8, 1.25)            # a snapshot counts for horizon H if secs_left is within 0.8H..1.25H
MAIN_H = "1h"
BUCKETS = [(0, 5), (5, 15), (15, 30), (30, 50), (50, 70), (70, 85), (85, 95), (95, 100)]
PAY_BUCKETS = [(1, 10), (10, 25), (25, 45), (45, 55), (55, 75), (75, 90), (90, 99)]
CONTRACTS = 10
MIN_EVENTS = 15                 # per side of the split, for a rule to count
MIN_CAT_EVENTS = 20             # to list a category on its own
STRONG_T = 2.0


def fee(price_c):
    """Kalshi's standard taker fee per contract (cents), rounded up per order of CONTRACTS.
    Some series charge less; this is the conservative case."""
    p = price_c / 100
    return math.ceil(0.07 * CONTRACTS * p * (1 - p) * 100 - 1e-9) / CONTRACTS


def num(x):
    try:
        return float(x) if x not in ("", None) else None
    except ValueError:
        return None


def load(since_ts):
    res = {}
    rp = os.path.join(C.DATA, "results.csv")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8", errors="ignore") as f:
            for r in csv.DictReader(f):
                ct = num(r.get("close_ts"))
                if r.get("result") in ("yes", "no") and ct and (since_ts is None or ct >= since_ts):
                    res[r["ticker"]] = {"y": 1 if r["result"] == "yes" else 0, "event": r["event"],
                                        "series": r["series"], "cat": r.get("category") or "Other",
                                        "close_ts": ct}
    best = {}                   # (ticker, horizon) -> (distance, bid, ask)
    for path in sorted(glob.glob(os.path.join(C.DATA, "snapshots", "snap_*.csv"))):
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read().replace("\x00", "")
        for r in csv.DictReader(text.splitlines()):
            tk = r.get("ticker")
            if tk not in res:
                continue
            left, yb, ya = num(r.get("secs_left")), num(r.get("yes_bid")), num(r.get("yes_ask"))
            if left is None or yb is None or ya is None or not (0 < yb <= ya < 100):
                continue
            for name, h in HORIZONS:
                if WINDOW[0] * h <= left <= WINDOW[1] * h:
                    d = abs(left - h)
                    k = (tk, name)
                    if k not in best or d < best[k][0]:
                        best[k] = (d, yb, ya)
    obs = defaultdict(list)     # horizon -> list of dicts
    for (tk, name), (_, yb, ya) in best.items():
        r = res[tk]
        obs[name].append({"tk": tk, "event": r["event"], "cat": r["cat"], "series": r["series"],
                          "close_ts": r["close_ts"], "y": r["y"], "bid": yb, "ask": ya,
                          "mid": (yb + ya) / 2})
    return res, obs


def calibration(P, obs):
    P("\n== 1. Calibration: market price vs what actually happened ==")
    P("   (yes% should be close to the price. * = gap larger than 2x its uncertainty)")
    for name, _ in HORIZONS:
        o = obs.get(name, [])
        if len(o) < 30:
            P(f"\n  {name} before close: not enough data yet ({len(o)} markets)")
            continue
        P(f"\n  {name} before close  ({len(o)} markets, {len(set(x['event'] for x in o))} events)")
        P("    price range   markets  events   avg price   yes%    gap")
        for lo, hi in BUCKETS:
            b = [x for x in o if lo <= x["mid"] < hi or (hi == 100 and x["mid"] == 100)]
            if not b:
                continue
            ev = len(set(x["event"] for x in b))
            avg = statistics.mean(x["mid"] for x in b)
            yes = 100 * statistics.mean(x["y"] for x in b)
            gap = yes - avg
            p = min(max(avg / 100, 0.01), 0.99)
            se = 100 * math.sqrt(p * (1 - p) / max(ev, 1))
            flag = " *" if abs(gap) > 2 * se and ev >= 10 else ""
            P(f"    {lo:>3}-{hi:<3}c     {len(b):6d}  {ev:6d}     {avg:5.1f}c   {yes:5.1f}%  {gap:+5.1f}{flag}")


def by_category(P, obs):
    o = obs.get(MAIN_H, [])
    P(f"\n== 2. Accuracy by category, {MAIN_H} before close ==")
    P("   Brier: lower is better (0 = perfect, 0.25 = coin flip). Bias: + means YES happened more than priced.")
    if len(o) < 30:
        P("  Not enough data yet.")
        return
    cats = defaultdict(list)
    for x in o:
        cats[x["cat"]].append(x)
    P("    category                     markets  events   Brier   bias   median spread")
    for cat, b in sorted(cats.items(), key=lambda kv: -len(kv[1])):
        ev = len(set(x["event"] for x in b))
        if ev < 5:
            continue
        brier = statistics.mean((x["mid"] / 100 - x["y"]) ** 2 for x in b)
        bias = 100 * statistics.mean(x["y"] - x["mid"] / 100 for x in b)
        spread = statistics.median(x["ask"] - x["bid"] for x in b)
        P(f"    {cat[:28]:<28} {len(b):7d}  {ev:6d}   {brier:.4f}  {bias:+5.1f}c   {spread:4.1f}c")


def trade(x, side):
    """Buy one contract of `side` at the ask, hold to settlement. Returns (price paid, pnl in cents)."""
    price = x["ask"] if side == "yes" else 100 - x["bid"]
    won = x["y"] == (1 if side == "yes" else 0)
    return price, (100 if won else 0) - price - fee(price)


def stats(per_event, avg_price):
    """per_event: list of mean pnl per event. Returns n, mean, t.
    The spread used for t never goes below half of what a fair bet at that price would have,
    so a short run of wins on 95c favorites can't look like proof."""
    n = len(per_event)
    if n == 0:
        return 0, 0.0, 0.0
    m = statistics.mean(per_event)
    q = min(max(avg_price / 100, 0.01), 0.99)
    sd = max(statistics.pstdev(per_event) if n > 1 else 0, 50 * math.sqrt(q * (1 - q)))
    return n, m, m / sd * math.sqrt(n)


def rules(P, obs):
    P("\n== 3. Trading test: buy at the ask, hold to settlement, taker fee included ==")
    all_ev = sorted({(x["close_ts"], x["event"]) for o in obs.values() for x in o})
    if len(all_ev) < 3 * MIN_EVENTS:
        P(f"  Not enough settled events yet ({len(all_ev)}; need {3 * MIN_EVENTS}+). Keep recording.")
        return
    cut = all_ev[int(len(all_ev) * 2 / 3)][0]
    P(f"  TRAIN = events closing before {datetime.fromtimestamp(cut).strftime('%Y-%m-%d %H:%M')}"
      f" (first 2/3), TEST = the rest (never used for choosing)")
    cat_events = defaultdict(set)
    for o in obs.values():
        for x in o:
            cat_events[x["cat"]].add(x["event"])
    groups = ["ALL"] + sorted(c for c, e in cat_events.items() if len(e) >= MIN_CAT_EVENTS)
    out = []
    for g in groups:
        for name, _ in HORIZONS:
            o = [x for x in obs.get(name, []) if g == "ALL" or x["cat"] == g]
            for side in ("yes", "no"):
                for lo, hi in PAY_BUCKETS:
                    tr, te, prices = defaultdict(list), defaultdict(list), []
                    for x in o:
                        price, pnl = trade(x, side)
                        if lo <= price < hi:
                            (tr if x["close_ts"] < cut else te)[x["event"]].append(pnl)
                            prices.append(price)
                    ap = statistics.mean(prices) if prices else 50
                    a = stats([statistics.mean(v) for v in tr.values()], ap)
                    b = stats([statistics.mean(v) for v in te.values()], ap)
                    out.append(((g, name, side, lo, hi), a, b))
    tested = len(out)
    P(f"  {tested} rules tested. By chance alone, about {tested // 20} of them will look good somewhere.")
    P("\n    rule                                           TRAIN: events  avg c   t    |  TEST: events  avg c   t")
    ranked = sorted([r for r in out if r[1][0] >= MIN_EVENTS], key=lambda r: -r[1][2])
    for (g, name, side, lo, hi), a, b in ranked[:15]:
        label = f"{g[:20]}, {name}, buy {side.upper()} at {lo}-{hi}c"
        P(f"    {label:<46} {a[0]:6d}  {a[1]:+6.2f} {a[2]:+5.1f}  |  {b[0]:6d}  {b[1]:+6.2f} {b[2]:+5.1f}")
    picks = [r for r in ranked if r[1][1] > 0 and r[1][2] >= STRONG_T]
    good = [r for r in picks if r[2][0] >= MIN_EVENTS and r[2][1] > 0 and r[2][2] >= STRONG_T]
    maybe = [r for r in picks if r not in good and r[2][0] >= MIN_EVENTS and r[2][1] > 0 and r[2][2] >= 1.0]
    P("")
    if maybe and not good:
        for (g, name, side, lo, hi), a, b in maybe[:5]:
            P(f"PROMISING, NEEDS MORE DATA: {g}, {name} before close, buy {side.upper()} at {lo}-{hi}c: "
              f"TRAIN {a[1]:+.2f}c (t {a[2]:+.1f}), TEST {b[1]:+.2f}c over {b[0]} events (t {b[2]:+.1f}).")
        P("  Strong on TRAIN and still positive on TEST, but not strong enough on TEST yet.")
        P("  Keep recording and re-run. Do not trade on this.")
    elif good:
        for (g, name, side, lo, hi), a, b in good[:5]:
            P(f"WORTH A CLOSER LOOK: {g}, {name} before close, buy {side.upper()} at {lo}-{hi}c: "
              f"TRAIN {a[1]:+.2f}c (t {a[2]:+.1f}), TEST {b[1]:+.2f}c over {b[0]} events (t {b[2]:+.1f}).")
        P("  A lead, not proof. Keep recording, re-run, and see if it holds on new data.")
    elif picks:
        P(f"NO RELIABLE EDGE: {len(picks)} rule(s) looked strong on TRAIN, but none held up on TEST "
          f"with {MIN_EVENTS}+ events.")
    else:
        P("NO EDGE YET: no rule was strong on TRAIN. Keep recording.")


def main():
    ap = argparse.ArgumentParser(description="Analyze recorded Kalshi markets.")
    ap.add_argument("--since", help='only markets closing after this local time, "YYYY-MM-DD" or "YYYY-MM-DD HH:MM"')
    ap.add_argument("--last", type=float, help="only markets that closed in the last N hours")
    ap.add_argument("--data", default=None, help="data folder (default: ../data)")
    a = ap.parse_args()
    if a.data:
        C.set_data_dir(a.data)
    since = None
    if a.since:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                since = datetime.strptime(a.since, fmt).timestamp()
                break
            except ValueError:
                pass
        else:
            sys.exit(f'Could not read --since "{a.since}". Use "YYYY-MM-DD" or "YYYY-MM-DD HH:MM".')
    elif a.last:
        since = time.time() - a.last * 3600

    res, obs = load(since)
    out = []
    P = out.append
    n_ev = len({r["event"] for r in res.values()})
    P(f"KALSHI MARKET SCANNER  |  {len(res)} settled markets in {n_ev} events"
      + (f"  |  since {datetime.fromtimestamp(since).strftime('%Y-%m-%d %H:%M')}" if since else ""))
    P("  Markets in the same event (e.g. one price ladder) move together, so events are the real sample size.")
    if not res:
        P("\nNo settled markets yet. Run record.py and let it collect for a day.")
    else:
        calibration(P, obs)
        by_category(P, obs)
        rules(P, obs)
    text = "\n".join(out)
    print(text)
    path = os.path.join(C.DATA, "scanner_report.txt")
    try:
        os.makedirs(C.DATA, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\nReport saved: {path}")
    except OSError as e:
        print(f"\n(could not save report: {e})")


if __name__ == "__main__":
    main()
