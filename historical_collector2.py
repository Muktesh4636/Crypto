"""
Extended historical backfill:
  1. Binance       — 4yr daily OHLCV for 30 crypto coins
  2. yfinance      — 5yr Gold, S&P 500, NASDAQ, DXY, VIX, Bitcoin ETFs, Oil, Silver
  3. Fear & Greed  — full history from alternative.me
  4. GDELT         — crypto + macro + geopolitical news (2022-now), 24 diverse queries
"""
import os, sys, time, logging, requests
from datetime import datetime, timezone, timedelta, date
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
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122 Safari/537.36',
    'Accept': 'application/json,*/*',
}

# ── Crypto coins (Binance) ────────────────────────────────────────────────────
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

# ── Market symbols via yfinance ───────────────────────────────────────────────
# (our_label, yahoo_ticker, display_name)
MARKET_SYMBOLS = [
    ('GOLD',        'GC=F',       'Gold Futures (USD/oz)'),
    ('SILVER',      'SI=F',       'Silver Futures (USD/oz)'),
    ('OIL',         'CL=F',       'Crude Oil WTI (USD/bbl)'),
    ('SP500',       '^GSPC',      'S&P 500'),
    ('NASDAQ',      '^IXIC',      'NASDAQ Composite'),
    ('DXY',         'DX-Y.NYB',   'US Dollar Index'),
    ('VIX',         '^VIX',       'CBOE Volatility Index'),
    ('TREASURY10Y', '^TNX',       '10-Year Treasury Yield (%)'),
    ('GLD',         'GLD',        'SPDR Gold ETF'),
    ('IBIT',        'IBIT',       'iShares Bitcoin ETF (BlackRock)'),
    ('FBTC',        'FBTC',       'Fidelity Wise Origin Bitcoin ETF'),
    ('GBTC',        'GBTC',       'Grayscale Bitcoin Trust'),
    ('ARKB',        'ARKB',       'ARK 21Shares Bitcoin ETF'),
    ('BITB',        'BITB',       'Bitwise Bitcoin ETF'),
]

# ── GDELT queries (24 diverse topics) ─────────────────────────────────────────
GDELT_QUERIES = [
    # Crypto direct
    'bitcoin price market',
    'ethereum cryptocurrency',
    'crypto regulation blockchain',
    'bitcoin ETF fund approval',
    'crypto exchange hack fraud',
    'DeFi NFT token',
    'stablecoin USDT USDC',
    # Macro / monetary policy
    'Federal Reserve interest rate',
    'inflation CPI monetary policy',
    'US dollar treasury yield bond',
    'gold price commodity rally',
    'stock market S&P 500 NASDAQ',
    'recession economic slowdown',
    # Geopolitical
    'Russia Ukraine war sanction economy',
    'China economy trade war technology ban',
    'Middle East oil geopolitical conflict',
    'war conflict global economy',
    # Country / institutional
    'El Salvador bitcoin legal tender country',
    'China crypto ban mining regulation',
    'US crypto bill congress senate',
    'Europe MiCA crypto regulation',
    'BlackRock Fidelity institutional bitcoin',
    'ETF bitcoin fund Wall Street',
    # Central bank / currency
    'CBDC central bank digital currency',
    'dollar collapse currency crisis reserve',
]

# ── Sentiment ─────────────────────────────────────────────────────────────────
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _sia = SentimentIntensityAnalyzer()
    def sentiment(t):
        s = _sia.polarity_scores(t or '')['compound']
        return round(s, 4), 'positive' if s >= 0.05 else ('negative' if s <= -0.05 else 'neutral')
except Exception:
    def sentiment(t): return 0.0, 'neutral'

CRYPTO_KW = {
    'BTC':['bitcoin','btc'],'ETH':['ethereum','eth'],'BNB':['binance','bnb'],
    'SOL':['solana','sol'],'XRP':['ripple','xrp'],'ADA':['cardano','ada'],
    'DOGE':['dogecoin','doge'],'AVAX':['avalanche','avax'],'DOT':['polkadot','dot'],
}
def find_currencies(text):
    t = text.lower()
    return [s for s, kws in CRYPTO_KW.items() if any(k in t for k in kws)]

def categorize(title):
    t = title.lower()
    if any(w in t for w in ['etf','blackrock','fidelity','grayscale','institutional','fund']): return 'etf'
    if any(w in t for w in ['regulat','sec','cftc','law','ban','government','congress','senate']): return 'regulation'
    if any(w in t for w in ['hack','stolen','exploit','scam','fraud','breach']): return 'security'
    if any(w in t for w in ['fed','inflation','interest rate','monetary','gdp','treasury','cpi']): return 'macro'
    if any(w in t for w in ['war','sanction','conflict','geopolit','ukraine','russia','china','middle east']): return 'geopolitics'
    if any(w in t for w in ['gold','silver','oil','commodity','opec']): return 'commodities'
    if any(w in t for w in ['halving','mining','hash','network','upgrade','fork']): return 'technical'
    return 'general'

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
            VALUES (:source, :title, :summary, :url, :pub, :score,
                    :label, :coins, 'historical', :category)
            ON CONFLICT (url) DO NOTHING
        """), dict(source=source, title=title[:500], summary=(summary or '')[:1000],
                   url=url[:1000], pub=pub_dt, score=score, label=label,
                   coins=coins, category=category))
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        log.debug(f'Article save error: {e}')
        return False

# ── 1. Binance crypto prices ──────────────────────────────────────────────────
def collect_binance_prices(db):
    log.info('=== Binance historical prices (Jan 2022 → today) ===')
    start_ms = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    total    = 0
    for symbol, _ in COINS:
        url = (f'https://api.binance.com/api/v3/klines'
               f'?symbol={symbol}USDT&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=1500')
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200: continue
            klines = resp.json()
            if not klines: continue
            count = 0
            for k in klines:
                dt = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date()
                try:
                    db.execute(text("""
                        INSERT INTO price_history_daily (symbol,date,open_usd,high_usd,low_usd,close_usd,volume_usd)
                        VALUES (:sym,:date,:o,:h,:l,:c,:v) ON CONFLICT (symbol,date) DO NOTHING
                    """), dict(sym=symbol, date=dt, o=float(k[1]), h=float(k[2]),
                               l=float(k[3]), c=float(k[4]), v=float(k[5])))
                    count += 1
                except Exception:
                    pass
            db.commit()
            total += count
            log.info(f'  {symbol}: {count} days')
        except Exception as e:
            log.error(f'  Binance {symbol}: {e}')
        time.sleep(0.3)
    log.info(f'=== Binance done: {total} records ===')
    return total

# ── 2. Yahoo Finance market data via yfinance ─────────────────────────────────
def collect_market_data(db):
    log.info('=== yfinance market data (Gold, Stocks, ETFs, DXY, VIX...) ===')
    try:
        import yfinance as yf
    except ImportError:
        log.error('yfinance not installed — skipping market data')
        return 0

    total = 0
    for symbol, ticker, name in MARKET_SYMBOLS:
        try:
            data = yf.download(ticker, period='5y', interval='1d',
                               progress=False, auto_adjust=True)
            if data.empty:
                log.warning(f'  {symbol} ({ticker}): no data returned')
                time.sleep(1)
                continue

            count = 0
            for dt_idx, row in data.iterrows():
                dt = dt_idx.date() if hasattr(dt_idx, 'date') else dt_idx

                # Handle multi-level columns from yfinance
                def get_val(col):
                    try:
                        v = row[col]
                        if hasattr(v, 'iloc'):  # Series
                            v = v.iloc[0]
                        return float(v) if v is not None and str(v) != 'nan' else None
                    except Exception:
                        return None

                close = get_val('Close')
                if close is None:
                    continue
                open_  = get_val('Open')  or close
                high   = get_val('High')  or close
                low    = get_val('Low')   or close
                volume = get_val('Volume')

                try:
                    db.execute(text("""
                        INSERT INTO market_data
                            (symbol, name, date, open_price, high_price, low_price, close_price, volume)
                        VALUES (:sym, :name, :date, :o, :h, :l, :c, :v)
                        ON CONFLICT (symbol, date) DO UPDATE SET
                            close_price = EXCLUDED.close_price,
                            open_price  = EXCLUDED.open_price,
                            high_price  = EXCLUDED.high_price,
                            low_price   = EXCLUDED.low_price,
                            volume      = EXCLUDED.volume
                    """), dict(sym=symbol, name=name, date=dt,
                               o=open_, h=high, l=low, c=close, v=volume))
                    count += 1
                except Exception as e:
                    db.rollback()
                    log.debug(f'    DB error {symbol} {dt}: {e}')

            db.commit()
            total += count
            log.info(f'  {symbol} ({ticker}): {count} days')
        except Exception as e:
            log.error(f'  {symbol} ({ticker}) error: {e}')
        time.sleep(1)

    log.info(f'=== Market data done: {total} records ===')
    return total

# ── 3. Fear & Greed Index ─────────────────────────────────────────────────────
def collect_fear_greed(db):
    log.info('=== Fear & Greed Index (alternative.me) ===')
    try:
        resp = requests.get('https://api.alternative.me/fng/?limit=1826&format=json',
                            headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            log.warning(f'F&G HTTP {resp.status_code}')
            return 0
        items = resp.json().get('data', [])
        count = 0
        for item in items:
            try:
                dt  = date.fromtimestamp(int(item['timestamp']))
                val = int(item['value'])
                cls = item.get('value_classification', '')
                db.execute(text("""
                    INSERT INTO fear_greed (date, value, classification)
                    VALUES (:date, :value, :cls)
                    ON CONFLICT (date) DO UPDATE SET value=EXCLUDED.value, classification=EXCLUDED.classification
                """), dict(date=dt, value=val, cls=cls))
                count += 1
            except Exception:
                pass
        db.commit()
        log.info(f'  Fear & Greed: {count} days saved')
        return count
    except Exception as e:
        log.error(f'  Fear & Greed error: {e}')
        return 0

# ── 4. GDELT historical news ──────────────────────────────────────────────────
def collect_gdelt_chunk(db, start_dt, end_dt, query):
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={requests.utils.quote(query)}"
        "&mode=artlist&maxrecords=250"
        f"&startdatetime={start_dt.strftime('%Y%m%d%H%M%S')}"
        f"&enddatetime={end_dt.strftime('%Y%m%d%H%M%S')}"
        "&format=json&sort=DateDesc&sourcelang=english"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=45)
        if resp.status_code == 429:
            log.warning('GDELT rate-limited — sleeping 120s')
            time.sleep(120); return 0
        if resp.status_code != 200:
            time.sleep(15); return 0
        if not resp.text.strip():
            time.sleep(20); return 0
        data     = resp.json()
        articles = data.get('articles', [])
        count    = 0
        for art in articles:
            title   = art.get('title', '').strip()
            url_art = art.get('url', '').strip()
            domain  = art.get('domain', 'GDELT')
            seendate= art.get('seendate', '')
            try:
                pub_dt = datetime.strptime(seendate, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            except Exception:
                pub_dt = start_dt
            if save_article(db, domain, title, '', url_art, pub_dt, categorize(title)):
                count += 1
        return count
    except requests.exceptions.Timeout:
        time.sleep(20); return 0
    except Exception as e:
        log.error(f'GDELT error: {e}'); time.sleep(10); return 0

def collect_gdelt_historical(db):
    log.info('=== GDELT historical news (Jan 2022 → today, 26 query types) ===')
    total    = 0
    current  = datetime(2022, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)
    batch    = 0

    while current < end_date:
        chunk_end = min(current + timedelta(days=14), end_date)
        query     = GDELT_QUERIES[batch % len(GDELT_QUERIES)]
        count     = collect_gdelt_chunk(db, current, chunk_end, query)
        total    += count
        batch    += 1
        log.info(f'  Batch {batch}: {current.strftime("%Y-%m-%d")}→{chunk_end.strftime("%Y-%m-%d")} '
                 f'"{query[:35]}" +{count} (total:{total})')
        current = chunk_end
        time.sleep(15)

    log.info(f'=== GDELT done: {total} historical news articles ===')
    return total

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    db = SessionLocal()

    price_count  = db.execute(text('SELECT COUNT(*) FROM price_history_daily')).scalar()
    market_count = db.execute(text('SELECT COUNT(*) FROM market_data')).scalar()
    fg_count     = db.execute(text('SELECT COUNT(*) FROM fear_greed')).scalar()
    hist_news    = db.execute(text("SELECT COUNT(*) FROM news_articles WHERE data_source='historical'")).scalar()

    log.info(f'Existing: {price_count} crypto | {market_count} market | {fg_count} F&G | {hist_news} hist-news')

    if price_count < 40000:
        collect_binance_prices(db)
    else:
        log.info(f'Crypto prices already populated ({price_count}) — skipping Binance')

    if market_count < 5000:
        collect_market_data(db)
    else:
        log.info(f'Market data already populated ({market_count}) — skipping yfinance')

    if fg_count < 500:
        collect_fear_greed(db)
    else:
        log.info(f'Fear & Greed already populated ({fg_count}) — skipping')

    collect_gdelt_historical(db)

    # Final summary
    log.info('=== BACKFILL COMPLETE ===')
    for k, q in [
        ('crypto_prices',  'SELECT COUNT(*) FROM price_history_daily'),
        ('market_data',    'SELECT COUNT(*) FROM market_data'),
        ('fear_greed',     'SELECT COUNT(*) FROM fear_greed'),
        ('hist_news',      "SELECT COUNT(*) FROM news_articles WHERE data_source='historical'"),
    ]:
        log.info(f'  {k}: {db.execute(text(q)).scalar()}')
    db.close()

if __name__ == '__main__':
    main()
