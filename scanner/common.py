"""Shared helpers: Kalshi public API client, paths, CSV writing, number parsing."""
import csv, json, os, sys, time
from datetime import datetime, timezone

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(ROOT, "data")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "kalshi-market-scanner (research)"})
MIN_GAP = 0.12            # seconds between API calls (Kalshi's basic limit is ~20 reads/s)
_last_call = [0.0]


def set_data_dir(path):
    global DATA
    DATA = os.path.abspath(path)


def api(path, **params):
    """GET a Kalshi public endpoint. Retries on rate limits and network errors."""
    tries = 8
    for attempt in range(tries):
        wait = MIN_GAP - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        backoff = min(30, 2 ** attempt)
        try:
            r = SESSION.get(BASE + path, params=params, timeout=20)
        except requests.RequestException:
            if attempt == tries - 1:
                raise
            time.sleep(backoff)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            try:
                backoff = max(backoff, float(r.headers.get("Retry-After", 0)))
            except ValueError:
                pass
            time.sleep(backoff)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Kalshi kept refusing {path} (rate limited)")


def cents(m, key):
    """A '_dollars' price field as cents (float), or None."""
    v = m.get(key + "_dollars")
    if v in (None, ""):
        v = m.get(key)
        return float(v) if v not in (None, "") else None
    return round(float(v) * 100, 2)


def qty(m, key):
    """A '_fp' count field (contracts) as float, or 0."""
    for k in (key + "_fp", key):
        v = m.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except ValueError:
                pass
    return 0.0


def ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None


def utc_day(t=None):
    return datetime.fromtimestamp(t or time.time(), timezone.utc).strftime("%Y%m%d")


def append_rows(path, cols, rows):
    """Append rows to a CSV, writing the header once. Retries if the file is briefly locked."""
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for attempt in range(10):
        try:
            new = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(cols)
                w.writerows(rows)
            return
        except PermissionError:
            time.sleep(0.5)
    print(f"  could not write {os.path.basename(path)} (file locked) - {len(rows)} rows skipped")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def series_of(event_ticker):
    return (event_ticker or "").split("-")[0]
