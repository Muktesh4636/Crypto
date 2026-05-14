# CryptoIntel — Self-Hosted Crypto Intelligence Dashboard

Full-stack crypto news aggregator, price tracker, manipulation detector, and historical analysis dashboard.

**Live at:** `crypto.pravoo.in`

---

## What it does

- **News** — Collects from 12+ RSS feeds (CoinDesk, CoinTelegraph, Decrypt, AMBCrypto, etc.) every 30 min, with full article text stored in DB
- **Prices** — Top 100 coins tracked every 15 min via CoinGecko
- **History** — 4 years of daily OHLCV for 30 coins via Binance API
- **Macro Events** — 98 hand-curated events (ETF approvals, FTX collapse, Fed decisions, halving, etc.)
- **Sentiment** — VADER sentiment analysis on all news (positive/negative/neutral)
- **Alerts** — Pump & dump, volume spike, manipulation detection
- **Reddit** — Community posts from crypto subreddits (needs OAuth)

---

## Quick Deploy

```bash
# 1. Clone or copy these files to your machine
git clone <repo> && cd crypto-intel

# 2. Set your server details
export SERVER_HOST="your.server.ip"
export SERVER_USER="root"

# 3. Full install (takes ~5 min)
chmod +x deploy.sh
./deploy.sh

# 4. Load 4 years of historical data (runs in background, ~2 hrs)
./deploy.sh --backfill
```

---

## Commands

| Command | Description |
|---|---|
| `./deploy.sh` | Full fresh install |
| `./deploy.sh --update` | Redeploy code only (keeps DB) |
| `./deploy.sh --backfill` | Collect 4-year historical data |
| `./deploy.sh --status` | Show DB stats and service health |

---

## Optional API Keys

Create a `.env` file (copy from `.env.example`) to enable extra sources:

```bash
cp .env.example .env
# Edit .env with your keys
source .env
./deploy.sh
```

| Key | Where to get | What it enables |
|---|---|---|
| `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` | [reddit.com/prefs/apps](https://reddit.com/prefs/apps) → create "script" app | Reddit posts from crypto subreddits |
| `CRYPTOPANIC_TOKEN` | [cryptopanic.com/developers/api](https://cryptopanic.com/developers/api/) | Better crypto news feed |

---

## Stack

| Component | Technology |
|---|---|
| Backend API | Python 3.12 + FastAPI + Uvicorn |
| Database | PostgreSQL 16 |
| Data collection | APScheduler (runs every 15/20/30/60 min) |
| Article extraction | trafilatura + newspaper4k + BeautifulSoup |
| Sentiment analysis | VADER (local, no API needed) |
| Price data | Binance API (free, no key) + CoinGecko |
| News sources | 12+ RSS feeds + CryptoPanic + GDELT |
| Historical data | Binance + CryptoCompare (4 years, 30 coins) |
| Frontend | Vanilla JS + Chart.js (no build step) |
| Web server | Nginx |
| Process manager | systemd |

---

## Server Requirements

- Ubuntu 20.04+ (tested on 24.04)
- 2+ GB RAM
- 20+ GB disk (current usage ~2GB, budget 100GB)
- Python 3.10+
- Nginx + PostgreSQL (installed automatically by deploy script)

---

## Services

Two systemd services run permanently:

- **`crypto-api`** — FastAPI backend on port 8765, proxied by Nginx
- **`crypto-scheduler`** — Collects news (30min), prices (15min), content (20min), Reddit (60min)

```bash
# Check services
systemctl status crypto-api crypto-scheduler

# View logs
tail -f /var/www/crypto.pravoo.in/logs/scheduler.log
tail -f /var/www/crypto.pravoo.in/logs/api.log

# Restart
systemctl restart crypto-api crypto-scheduler
```

---

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/health` | Health check |
| `GET /api/stats` | Overall stats |
| `GET /api/news` | News list (paginated, filterable) |
| `GET /api/news/{id}` | Full article with stored content |
| `GET /api/news/sentiment-trend` | Hourly sentiment over 72h |
| `GET /api/prices` | Live top-100 prices |
| `GET /api/prices/top-movers` | Gainers and losers |
| `GET /api/prices/history/{symbol}` | Live price history |
| `GET /api/history/prices/{symbol}` | 4-year daily OHLCV |
| `GET /api/history/prices/{symbol}/stats` | ATH, ATL, return |
| `GET /api/history/events` | Macro events (filterable) |
| `GET /api/history/events/timeline` | Price + events overlay data |
| `GET /api/history/sentiment-vs-price` | Sentiment vs price correlation |
| `GET /api/history/coins` | All coins with historical data |
| `GET /api/alerts` | Manipulation & spike alerts |
| `GET /api/reddit` | Reddit posts |

---

## File Structure

```
/var/www/crypto.pravoo.in/
├── backend/
│   ├── venv/                      # Python virtualenv
│   ├── app/
│   │   ├── main.py                # FastAPI app
│   │   ├── database.py            # DB connection
│   │   └── routers/
│   │       ├── news.py            # News API
│   │       ├── prices.py          # Live prices API
│   │       ├── alerts.py          # Alerts API
│   │       ├── reddit.py          # Reddit API
│   │       └── history.py         # Historical data API
│   ├── collectors/
│   │   ├── rss_collector.py       # 12 RSS feeds
│   │   ├── coingecko.py           # Price data + spike detection
│   │   ├── reddit_collector.py    # Reddit + CryptoPanic
│   │   └── content_fetcher.py     # Full article text scraper
│   ├── analyzers/
│   │   └── manipulation.py        # Pump/dump detection
│   ├── scheduler.py               # APScheduler (runs all collectors)
│   ├── historical_collector.py    # 4-year backfill (Binance + GDELT)
│   ├── seed_events.py             # 98 macro events seeder
│   └── requirements.txt
├── frontend/
│   └── index.html                 # Full dashboard (no build needed)
└── logs/
    ├── api.log
    ├── scheduler.log
    └── historical.log
```

---

## Enabling HTTPS (SSL)

```bash
ssh root@your.server.ip
apt install certbot python3-certbot-nginx -y
certbot --nginx -d crypto.pravoo.in
```

Certbot auto-renews every 90 days.

---

## Adding More News Sources

Edit `/var/www/crypto.pravoo.in/backend/collectors/rss_collector.py` and add to `RSS_FEEDS`:

```python
{"name": "YourSource", "url": "https://yoursite.com/feed.xml"},
```

Then `systemctl restart crypto-scheduler`.
