"""
record.py - records many Kalshi markets as they approach their close.

Every DISCOVER_MIN minutes it finds open markets that close within HORIZON_H hours
and actually trade (two-sided book, some volume). Every SNAP_MIN minutes it saves a
price snapshot of each one. After a market closes it records the result.

    python record.py                 # run until Ctrl+C
    python record.py --minutes 30    # stop after 30 minutes (for a quick test)

Writes (in data/):
    snapshots/snap_YYYYMMDD.csv   one row per market per snapshot (UTC day per file)
    results.csv                   one row per settled market
    series.json                   series -> category (cached)
    state.json                    markets being watched (so a restart picks up where it left off)

Research only. Places no orders. Needs no API key.
"""
import argparse, os, sys, time

import common as C

# ============================ settings ============================
HORIZON_H    = 26          # watch markets that close within this many hours
DISCOVER_MIN = 30          # look for new markets this often
SNAP_MIN     = 5           # snapshot every watched market this often
MIN_VOL_24H  = 50          # contracts traded in the last 24 h to start watching a market
SKIP_SERIES  = ("KXMVE",)  # custom combo / parlay markets: almost never trade
GIVE_UP_H    = 72          # stop waiting for a result this long after close
# ==================================================================

SNAP_COLS = ["ts", "ticker", "event", "series", "close_ts", "secs_left", "status",
             "yes_bid", "yes_ask", "yes_bid_sz", "yes_ask_sz", "last",
             "volume", "volume_24h", "open_interest"]
RES_COLS = ["ticker", "event", "series", "category", "close_ts", "settled_ts", "result"]


def paths():
    return {
        "snap_dir": os.path.join(C.DATA, "snapshots"),
        "results": os.path.join(C.DATA, "results.csv"),
        "series": os.path.join(C.DATA, "series.json"),
        "state": os.path.join(C.DATA, "state.json"),
    }


def tradeable(m):
    yb, ya = C.cents(m, "yes_bid"), C.cents(m, "yes_ask")
    return (yb is not None and ya is not None and 0 < yb <= ya < 100
            and C.qty(m, "volume_24h") >= MIN_VOL_24H)


def discover(watch):
    """Add open, trading markets that close within HORIZON_H hours. Returns how many were added."""
    now = int(time.time())
    cursor, added, seen, pages = None, 0, 0, 0
    while True:
        params = {"status": "open", "limit": 1000,
                  "min_close_ts": now + 60, "max_close_ts": now + HORIZON_H * 3600}
        if cursor:
            params["cursor"] = cursor
        j = C.api("/markets", **params)
        pages += 1
        for m in j.get("markets", []):
            seen += 1
            tk = m.get("ticker")
            series = C.series_of(m.get("event_ticker"))
            if not tk or tk in watch or series.startswith(SKIP_SERIES) or not tradeable(m):
                continue
            watch[tk] = {"event": m.get("event_ticker"), "series": series,
                         "close_ts": C.ts(m.get("close_time")), "done": False}
            added += 1
        cursor = j.get("cursor")
        if not cursor or pages >= 50:
            break
    return added, seen


def category(series, cache):
    if series not in cache:
        try:
            cache[series] = (C.api(f"/series/{series}").get("series") or {}).get("category") or ""
        except Exception:
            return ""
    return cache[series]


def fetch_batch(tickers):
    out = {}
    for i in range(0, len(tickers), 100):
        j = C.api("/markets", tickers=",".join(tickers[i:i + 100]), limit=1000)
        for m in j.get("markets", []):
            out[m["ticker"]] = m
    return out


def snapshot_and_settle(watch, cats, P):
    now = time.time()
    active = [tk for tk, w in watch.items() if not w["done"]]
    if not active:
        return 0, 0
    markets = fetch_batch(active)
    rows, res_rows = [], []
    for tk in active:
        w, m = watch[tk], markets.get(tk)
        if m is None:
            if w["close_ts"] and now > w["close_ts"] + GIVE_UP_H * 3600:
                w["done"] = True
            continue
        status = m.get("status", "")
        result = m.get("result") or ""
        if result in ("yes", "no") and status in ("finalized", "settled", "determined"):
            res_rows.append([tk, w["event"], w["series"], category(w["series"], cats),
                             int(w["close_ts"] or 0), int(C.ts(m.get("settlement_ts")) or now), result])
            w["done"] = True
            continue
        if w["close_ts"] and now > w["close_ts"] + GIVE_UP_H * 3600:
            w["done"] = True            # voided or never settled
            continue
        if status != "active":
            continue                    # closed, waiting for the result
        close_ts = C.ts(m.get("close_time")) or w["close_ts"]
        w["close_ts"] = close_ts
        rows.append([round(now, 1), tk, w["event"], w["series"], int(close_ts), int(close_ts - now), status,
                     C.cents(m, "yes_bid"), C.cents(m, "yes_ask"),
                     C.qty(m, "yes_bid_size"), C.qty(m, "yes_ask_size"), C.cents(m, "last_price"),
                     C.qty(m, "volume"), C.qty(m, "volume_24h"), C.qty(m, "open_interest")])
    C.append_rows(os.path.join(P["snap_dir"], f"snap_{C.utc_day(now)}.csv"), SNAP_COLS, rows)
    C.append_rows(P["results"], RES_COLS, res_rows)
    # forget finished markets after a day so state.json stays small
    for tk in [tk for tk, w in watch.items() if w["done"] and w["close_ts"] and now > w["close_ts"] + 86400]:
        del watch[tk]
    return len(rows), len(res_rows)


def main():
    ap = argparse.ArgumentParser(description="Record Kalshi markets as they approach close.")
    ap.add_argument("--minutes", type=float, default=0, help="stop after this many minutes (0 = run forever)")
    ap.add_argument("--data", default=None, help="data folder (default: ../data)")
    ap.add_argument("--snap-min", type=float, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args()
    global SNAP_MIN
    if a.snap_min:
        SNAP_MIN = a.snap_min
    if a.data:
        C.set_data_dir(a.data)
    P = paths()
    watch = C.load_json(P["state"], {})
    cats = C.load_json(P["series"], {})
    print(f"Recording to {C.DATA}  (no orders, no API key)  Ctrl+C to stop")
    print(f"Watching markets that close within {HORIZON_H} h. Snapshot every {SNAP_MIN} min, "
          f"new markets every {DISCOVER_MIN} min.")
    stop_at = time.time() + a.minutes * 60 if a.minutes else None
    next_disc = next_snap = 0
    try:
        while stop_at is None or time.time() < stop_at:
            now = time.time()
            if now >= next_disc:
                try:
                    added, seen = discover(watch)
                    next_disc = now + DISCOVER_MIN * 60
                    print(f"{time.strftime('%H:%M:%S')}  scanned {seen} open markets, +{added} new, "
                          f"watching {sum(1 for w in watch.values() if not w['done'])}")
                except Exception as e:
                    next_disc = now + 120
                    print(f"{time.strftime('%H:%M:%S')}  market scan failed: {str(e)[:100]}  (retry in 2 min)")
            if now >= next_snap:
                try:
                    n, r = snapshot_and_settle(watch, cats, P)
                    print(f"{time.strftime('%H:%M:%S')}  snapshot {n} markets, {r} new results")
                except Exception as e:
                    print(f"{time.strftime('%H:%M:%S')}  snapshot failed: {str(e)[:100]}  (will retry)")
                next_snap = now + SNAP_MIN * 60
                C.save_json(P["state"], watch)
                C.save_json(P["series"], cats)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    C.save_json(P["state"], watch)
    C.save_json(P["series"], cats)
    print("Stopped. State saved.")


if __name__ == "__main__":
    main()
