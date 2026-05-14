"""
Historical data collector — prices (Binance) + news (GDELT + CryptoPanic).
Covers Jan 2022 → today.
"""
import os, sys, time, logging, requests
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

DB_URL = os.getenv('DATABASE_URL', 'postgresql://crypto_user:CryptoSecure2024!@localhost/crypto_db')
engine = create_engine(DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
}

COINS = [
    ('BTC','bitcoin'),('ETH','ethereum'),('BNB','binancecoin'),
    ('SOL','solana'),('XRP','ripple'),('ADA','cardano'),
    ('DOGE','dogecoin'),('AVAX','avalanche-2'),('DOT','polkadot'),
    ('MATIC','matic-network'),('LINK','chainlink'),('LTC','litecoin'),
    ('SHIB','shiba-inu'),('TRX','tron'),('NEAR','near'),
    ('UNI','uniswap'),('ATOM','cosmos'),('XLM','stellar'),
    ('ETC','ethereum-classic'),('FIL','filecoin'),('AAVE','aave'),
    ('VET','vechain'),('ALGO','algorand'),('HBAR','hedera-hashgraph'),
    ('ICP','internet-computer'),('APT','aptos'),('ARB','arbitrum'),
    ('OP','optimism'),('INJ','injective-protocol'),('SUI','sui'),
]

# ── Sentiment ───────────────────────────────────────────────────────────────
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _sia = SentimentIntensityAnalyzer()
    def sentiment(text):
        if not text:
            return 0.0, 'neutral'
        s = _sia.polarity_scores(text)['compound']
        label = 'positive' if s >= 0.05 else ('negative' if s <= -0.05 else 'neutral')
        return round(s, 4), label
except Exception:
    def sentiment(text):
        return 0.0, 'neutral'

CRYPTO_KEYWORDS = {
    'BTC':['bitcoin','btc'],'ETH':['ethereum','eth'],'BNB':['binance','bnb'],
    'SOL':['solana','sol'],'XRP':['ripple','xrp'],'ADA':['cardano','ada'],
    'DOGE':['dogecoin','doge'],'AVAX':['avalanche','avax'],'DOT':['polkadot','dot'],
    'MATIC':['polygon','matic'],'LINK':['chainlink','link'],
}

def find_currencies(text):
    t = text.lower()
    return [sym for sym, kws in CRYPTO_KEYWORDS.items() if any(k in t for k in kws)]

def save_article(db, source, title, summary, url, pub_dt, category='general'):
    if not title or not url:
        return False
    score, label = sentiment(title + ' ' + (summary or ''))
    coins = find_currencies(title + ' ' + (summary or ''))
    try:
        db.execute(text("""
            INSERT INTO news_articles
                (source, title, summary, url, published_at, sentiment_score,
                 sentiment_label, currencies, data_source, category)
            VALUES
                (:source, :title, :summary, :url, :pub_dt, :score,
                 :label, :coins, 'historical', :category)
            ON CONFLICT (url) DO NOTHING
        """), dict(source=source, title=title[:500], summary=(summary or '')[:1000],
                   url=url[:1000], pub_dt=pub_dt, score=score, label=label,
                   coins=coins, category=category))
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        log.error(f'DB save error: {e}')
        return False

def categorize(title):
    t = title.lower()
    if any(w in t for w in ['etf','fund','institution','blackrock','fidelity','grayscale']): return 'etf'
    if any(w in t for w in ['regulat','sec','cftc','government','ban','law','legal','congress']): return 'regulation'
    if any(w in t for w in ['hack','stolen','exploit','breach','scam','fraud']): return 'security'
    if any(w in t for w in ['fed','inflation','interest rate','macro','economy','gdp']): return 'macro'
    if any(w in t for w in ['halving','mining','hash','network']): return 'technical'
    return 'general'

# ── GDELT news ──────────────────────────────────────────────────────────────
# Use simple single-term queries which GDELT handles reliably.
# Rotate queries to get diverse coverage.
GDELT_QUERIES = [
    'bitcoin',
    'ethereum cryptocurrency',
    'crypto regulation',
    'bitcoin ETF',
    'crypto market',
    'blockchain',
    'DeFi cryptocurrency',
    'crypto exchange',
]

def collect_gdelt_chunk(db, start_dt, end_dt, query):
    """Fetch one 2-week batch from GDELT for a single query."""
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={requests.utils.quote(query)}"
        "&mode=artlist&maxrecords=250"
        f"&startdatetime={start_dt.strftime('%Y%m%d%H%M%S')}"
        f"&enddatetime={end_dt.strftime('%Y%m%d%H%M%S')}"
        "&format=json&sort=DateDesc"
        "&sourcelang=english"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=45)
        if resp.status_code == 429:
            log.warning('GDELT rate-limited — sleeping 90s')
            time.sleep(90)
            return 0
        if resp.status_code != 200:
            log.warning(f'GDELT HTTP {resp.status_code}')
            time.sleep(10)
            return 0
        if not resp.text.strip():
            log.warning('GDELT empty response')
            time.sleep(15)
            return 0
        data = resp.json()
        articles = data.get('articles', [])
        count = 0
        for art in articles:
            title = art.get('title', '').strip()
            url_art = art.get('url', '').strip()
            domain = art.get('domain', 'GDELT')
            seendate = art.get('seendate', '')
            try:
                pub_dt = datetime.strptime(seendate, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            except Exception:
                pub_dt = start_dt
            if save_article(db, domain, title, '', url_art, pub_dt, categorize(title)):
                count += 1
        return count
    except requests.exceptions.Timeout:
        log.warning('GDELT timeout')
        time.sleep(20)
        return 0
    except Exception as e:
        log.error(f'GDELT error: {e}')
        time.sleep(10)
        return 0

def collect_gdelt_historical(db):
    log.info('=== Starting GDELT historical news collection (Jan 2022 → today) ===')
    total = 0
    start_date = datetime(2022, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)
    chunk_days = 14  # 2-week chunks

    current = start_date
    batch_num = 0

    while current < end_date:
        chunk_end = min(current + timedelta(days=chunk_days), end_date)

        # Rotate through queries for each chunk to get diverse coverage
        query = GDELT_QUERIES[batch_num % len(GDELT_QUERIES)]

        count = collect_gdelt_chunk(db, current, chunk_end, query)
        total += count
        batch_num += 1
        log.info(
            f'GDELT batch {batch_num}: {current.strftime("%Y-%m-%d")} → '
            f'{chunk_end.strftime("%Y-%m-%d")} | query="{query}" | '
            f'+{count} articles (running total: {total})'
        )

        current = chunk_end
        # Respect rate limits — 15s between requests
        time.sleep(15)

    log.info(f'=== GDELT done: {total} historical articles saved ===')
    return total

# ── CryptoPanic public news ─────────────────────────────────────────────────
def collect_cryptopanic_historical(db):
    """
    CryptoPanic public API (no key needed) — gives ~50 pages of news.
    Each page has 20 items. We paginate until we hit articles older than 2022.
    """
    log.info('=== Starting CryptoPanic historical news collection ===')
    token = os.getenv('CRYPTOPANIC_TOKEN', '')
    cutoff = datetime(2022, 1, 1, tzinfo=timezone.utc)
    total = 0
    url = f'https://cryptopanic.com/api/v1/posts/?public=true&kind=news&format=json'
    if token:
        url += f'&auth_token={token}'

    page = 1
    while True:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 429:
                log.warning('CryptoPanic rate limited — sleeping 60s')
                time.sleep(60)
                continue
            if resp.status_code != 200:
                log.warning(f'CryptoPanic HTTP {resp.status_code}')
                break
            data = resp.json()
            posts = data.get('results', [])
            if not posts:
                break

            oldest_in_page = None
            count = 0
            for post in posts:
                created = post.get('created_at', '')
                try:
                    pub_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                except Exception:
                    pub_dt = datetime.now(timezone.utc)

                if oldest_in_page is None or pub_dt < oldest_in_page:
                    oldest_in_page = pub_dt

                if pub_dt < cutoff:
                    continue

                title = post.get('title', '').strip()
                post_url = post.get('url', '').strip()
                domain = post.get('domain', 'cryptopanic')
                if save_article(db, domain, title, '', post_url, pub_dt, categorize(title)):
                    count += 1

            total += count
            log.info(f'CryptoPanic page {page}: +{count} articles (total: {total}), oldest: {oldest_in_page}')

            # Stop if we've gone back past 2022
            if oldest_in_page and oldest_in_page < cutoff:
                log.info('Reached 2022 cutoff — stopping CryptoPanic')
                break

            # Get next page URL
            next_url = data.get('next')
            if not next_url:
                break
            url = next_url
            page += 1
            time.sleep(3)

        except Exception as e:
            log.error(f'CryptoPanic error: {e}')
            break

    log.info(f'=== CryptoPanic done: {total} articles saved ===')
    return total

# ── Binance historical prices ────────────────────────────────────────────────
def collect_binance_prices(db):
    log.info('=== Starting Binance historical price collection (Jan 2022 → today) ===')
    start_ms = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    total = 0
    skipped = 0

    for symbol, _ in COINS:
        pair = f'{symbol}USDT'
        url = (
            f'https://api.binance.com/api/v3/klines'
            f'?symbol={pair}&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=1500'
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                log.warning(f'Binance {pair}: HTTP {resp.status_code}')
                # Try without USDT (some coins use BUSD or don't exist on Binance)
                skipped += 1
                continue
            klines = resp.json()
            if not klines:
                log.warning(f'Binance {pair}: no data')
                skipped += 1
                continue
            count = 0
            for k in klines:
                open_t = datetime.fromtimestamp(k[0]/1000, tz=timezone.utc).date()
                try:
                    db.execute(text("""
                        INSERT INTO price_history_daily
                            (symbol, date, open_usd, high_usd, low_usd, close_usd, volume_usd)
                        VALUES (:sym, :date, :o, :h, :l, :c, :v)
                        ON CONFLICT (symbol, date) DO NOTHING
                    """), dict(sym=symbol, date=open_t,
                               o=float(k[1]), h=float(k[2]), l=float(k[3]),
                               c=float(k[4]), v=float(k[5])))
                    count += 1
                except Exception:
                    pass
            db.commit()
            total += count
            log.info(f'Binance {symbol}: {count} days saved')
        except Exception as e:
            log.error(f'Binance {symbol} error: {e}')
        time.sleep(0.5)

    log.info(f'=== Binance done: {total} price records saved, {skipped} symbols skipped ===')
    return total

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    db = SessionLocal()

    # 1. Check what's already in the DB
    price_count = db.execute(text('SELECT COUNT(*) FROM price_history_daily')).scalar()
    hist_news_count = db.execute(
        text("SELECT COUNT(*) FROM news_articles WHERE data_source='historical'")
    ).scalar()

    log.info(f'Existing: {price_count} price records, {hist_news_count} historical news articles')

    # 2. Collect prices (skip if we already have a lot)
    if price_count < 40000:
        log.info('Collecting Binance historical prices...')
        collect_binance_prices(db)
    else:
        log.info(f'Price data already populated ({price_count} records) — skipping Binance')

    # 3. Collect historical news
    # CryptoPanic first (fast, reliable)
    log.info('Collecting CryptoPanic historical news...')
    collect_cryptopanic_historical(db)

    # Then GDELT (slower, more comprehensive)
    log.info('Collecting GDELT historical news (this takes ~2 hours)...')
    collect_gdelt_historical(db)

    # 4. Final summary
    final_price = db.execute(text('SELECT COUNT(*) FROM price_history_daily')).scalar()
    final_news = db.execute(
        text("SELECT COUNT(*) FROM news_articles WHERE data_source='historical'")
    ).scalar()
    log.info(f'=== BACKFILL COMPLETE: {final_price} price records | {final_news} historical articles ===')
    db.close()

if __name__ == '__main__':
    main()
