# Kalshi Market Scanner

*Which prediction markets does the crowd price well, and which does it not?*

My first project, [kalshi-bot-learning](https://github.com/zacharyalitwin-alt/kalshi-bot-learning), found that Kalshi's 15-minute Bitcoin market is fast and priced about right. This project asks the next question: **is that true everywhere?** Slower, less-watched markets (weather, oil, esports, city temperatures) are where pricing mistakes are more likely to survive.

The scanner records hundreds of Kalshi markets as they approach their close, waits for each result, then measures how well the market's price predicted it, category by category.

**Research only. Nothing here places trades or needs an API key.**

---

## What it measures

1. **Calibration.** When the market said 80%, did YES happen about 80% of the time? Checked at 24 hours, 6 hours, 1 hour and 15 minutes before close.
2. **Accuracy by category.** Brier score (0 = perfect, 0.25 = coin flip) and bias for each Kalshi category: Climate and Weather, Crypto, Financials, Sports, Economics and more.
3. **A trading test.** "Buy YES (or NO) at the ask when the price is in a range, and hold to settlement." Fees and the spread are included. Rules are chosen on the first 2/3 of events and judged on the last 1/3, which they never saw.

The verdict is one of three:

| Verdict | Meaning |
|---|---|
| **NO EDGE YET / NO RELIABLE EDGE** | Nothing held up on unseen data. |
| **PROMISING, NEEDS MORE DATA** | Strong on the training data and still positive on the test data, but not proven. Keep recording. |
| **WORTH A CLOSER LOOK** | Strong on both. A lead to re-check on new data, not a reason to trade. |

## Quick start

Python 3.10 or newer.

```bash
git clone https://github.com/zacharyalitwin-alt/kalshi-market-scanner.git
cd kalshi-market-scanner
pip install -r requirements.txt
cd scanner
python record.py
```

Leave the recorder running. After a day or more, open a second terminal in `scanner/`:

```bash
python analyze.py
```

The report prints on screen and is saved to `data/scanner_report.txt`.

```bash
python analyze.py --since "2026-11-10"   # only markets that closed after this date
python analyze.py --last 72              # only markets that closed in the last 72 hours
```

### How much data you need

| Recording | What it's good for |
|---|---|
| 1 day | Calibration tables start to fill in. |
| 3–4 days | Enough settled events for the trading test to run. |
| 2+ weeks | Category-level results worth trusting. |

## How it works

```
             every 30 min                    every 5 min                 after close
 Kalshi ──► find markets closing ──► snapshot each one's ──► record the result
 public     within 26 h that         bid, ask, volume            (yes / no)
 API        actually trade
                 │                          │                          │
                 └──────── state.json  snapshots/snap_*.csv      results.csv
                                                 │                    │
                                                 └──── analyze.py ────┘
```

- **Which markets:** open markets closing within 26 hours that have a two-sided book and at least 50 contracts traded in the last 24 hours. That's usually several hundred markets across about 200 series. Custom combo (parlay) markets are skipped because they almost never trade.
- **Load on Kalshi:** about one market scan every 30 minutes and one batched price check every 5 minutes, well inside the public rate limit. The recorder backs off automatically if Kalshi asks it to slow down.
- **Restarts are safe:** the list of watched markets is saved to `state.json`, so stopping and starting again picks up where it left off.

## Data files

All files are written to `data/` and stay local (see `.gitignore`).

**`snapshots/snap_YYYYMMDD.csv`**: one row per market per snapshot (UTC day per file)

| Column | Meaning |
|---|---|
| `ts` | Unix time of the snapshot |
| `ticker`, `event`, `series` | Market, its event (e.g. one price ladder), and its series |
| `close_ts`, `secs_left` | When the market closes, and seconds until then |
| `yes_bid`, `yes_ask` | Best prices in cents (the NO ask is `100 − yes_bid`) |
| `yes_bid_sz`, `yes_ask_sz` | Contracts available at those prices |
| `last`, `volume`, `volume_24h`, `open_interest` | Trading activity |

**`results.csv`**: one row per settled market: ticker, event, series, category, close time, settle time and result (`yes` / `no`).

## Method notes

- **Events, not markets, are the sample size.** Markets in the same event (for example, every strike in one Bitcoin price ladder) move together, so all statistics and the TRAIN/TEST split are done by event.
- **Fees:** Kalshi's standard taker fee, `ceil(0.07 × contracts × P × (1 − P))`, on every simulated trade. Some series charge less, so results are slightly conservative.
- **Multiple testing:** the trading test checks a few hundred rules. By chance alone about 1 in 20 will look good on any given split. The report says how many rules it tested so that's easy to keep in mind.
- **Fills** are simulated at the displayed ask. Larger orders than the displayed size would do worse.

## Related

- [kalshi-bot-learning](https://github.com/zacharyalitwin-alt/kalshi-bot-learning): the 15-minute Bitcoin market study that led to this project.

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

For research and education only. Not investment advice. Prediction markets carry real risk of loss.
