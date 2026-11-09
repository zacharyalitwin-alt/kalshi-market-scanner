# Project notes for Claude

Research tool that records many Kalshi markets and measures how well the crowd prices them, by category. Sister project to `kalshi-bot-learning`. See README.md.

## Rules

- **Research only.** No order placement, no API keys. Only Kalshi's public, unauthenticated GET endpoints.
- **Be gentle with the API.** Keep batching (`/markets?tickers=...`, 100 per call) and the backoff in `common.api()`. Don't shorten the scan/snapshot intervals in committed code.
- **Never commit data.** `data/` is ignored except `.gitkeep`.
- **Test the way the README says.** Events (not markets) are the unit; choose rules on TRAIN, judge on TEST; include the taker fee and spread; report how many rules were tested.
- **Report plainly.** If nothing holds up, say so.
- Explain things to the owner in plain language with Windows Command Prompt commands, one per code block.

## Layout

- `scanner/common.py` – API client (rate-limit backoff), price parsing (`*_dollars` strings -> cents, `*_fp` counts), CSV append with retry (the owner's folder is in OneDrive).
- `scanner/record.py` – discovery every 30 min, snapshots every 5 min, results after close; state in `data/state.json`.
- `scanner/analyze.py` – calibration, category accuracy, TRAIN/TEST trading test; writes `data/scanner_report.txt`.

## Kalshi API notes (checked Nov 2026)

- Market prices: `yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars` (strings in dollars). Sizes/volumes: `*_fp` strings.
- Open markets list with `status=open`; a market's own `status` reads `active`. Settled markets read `finalized` with `result` = `yes`/`no`.
- Category lives on the series (`/series/{ticker}`) and event, not the market.
- Skip `KXMVE*` (custom multi-leg combos).
