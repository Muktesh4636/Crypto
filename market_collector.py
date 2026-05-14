"""
Collects live and historical data for macro market indicators:
  Gold, S&P 500, NASDAQ, DXY, VIX, 10Y Treasury,
  Bitcoin ETFs (IBIT, FBTC, GBTC), Fear & Greed Index
"""
import logging, requests, time
from datetime import datetime, timezone, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text

log = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122 Safari/537.36',
    'Accept': 'application/json,text/html,*/*',
    'Accept-Language': 'en-US,en;q=0.9',
}

# (our_label, yahoo_ticker, display_name, category)
MARKET_SYMBOLS = [
    ('GOLD',        'GC=F',       'Gold Futures (USD/oz)',           'commodity'),
    ('SP500',       '^GSPC',      'S&P 500',                         'equity'),
    ('NASDAQ',      '^IXIC',      'NASDAQ Composite',                'equity'),
    ('DXY',         'DX-Y.NYB',   'US Dollar Index',                 'currency'),
    ('VIX',         '^VIX',       'CBOE Volatility Index',           'volatility'),
    ('TREASURY10Y', '^TNX',       '10-Year Treasury Yield',          'bonds'),
    ('GLD',         'GLD',        'SPDR Gold ETF',                   'etf'),
    ('IBIT',        'IBIT',       'iShares Bitcoin ETF (BlackRock)', 'btc_etf'),
    ('FBTC',        'FBTC',       'Fidelity Wise Origin Bitcoin ETF','btc_etf'),
    ('GBTC',        'GBTC',       'Grayscale Bitcoin Trust',         'btc_etf'),
    ('ARKB',        'ARKB',       'ARK 21Shares Bitcoin ETF',        'btc_etf'),
    ('BITB',        'BITB',       'Bitwise Bitcoin ETF',             'btc_etf'),
    ('OIL',         'CL=F',       'Crude Oil Futures (WTI)',         'commodity'),
    ('SILVER',      'SI=F',       'Silver Futures',                  'commodity'),
]

def _fetch_yahoo(ticker: str, period: str = '5y', interval: str = '1d') -> dict | None:
    """Fetch from Yahoo Finance with query1/query2 fallback."""
    for host in ('query1', 'query2'):
        url = f'https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}'
        try:
            resp = requests.get(url, headers=HEADERS,
                                params={'range': period, 'interval': interval, 'events': 'history'},
                                timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get('chart', {}).get('result', [])
                if results:
                    return results[0]
        except Exception as e:
            log.debug(f'Yahoo Finance {host} error for {ticker}: {e}')
        time.sleep(0.5)
    return None

def _save_market_rows(db: Session, symbol: str, name: str, result: dict) -> int:
    timestamps = result.get('timestamp', [])
    q = result.get('indicators', {}).get('quote', [{}])[0]
    opens   = q.get('open',   [])
    highs   = q.get('high',   [])
    lows    = q.get('low',    [])
    closes  = q.get('close',  [])
    volumes = q.get('volume', [])

    count = 0
    for i, ts in enumerate(timestamps):
        if ts is None:
            continue
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        dt = date.fromtimestamp(ts)
        o = opens[i]   if i < len(opens)   and opens[i]   is not None else close
        h = highs[i]   if i < len(highs)   and highs[i]   is not None else close
        l = lows[i]    if i < len(lows)    and lows[i]    is not None else close
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else None
        try:
            db.execute(text("""
                INSERT INTO market_data (symbol, name, date, open_price, high_price, low_price, close_price, volume)
                VALUES (:sym, :name, :date, :o, :h, :l, :c, :v)
                ON CONFLICT (symbol, date) DO UPDATE SET
                    close_price = EXCLUDED.close_price,
                    open_price  = EXCLUDED.open_price,
                    high_price  = EXCLUDED.high_price,
                    low_price   = EXCLUDED.low_price,
                    volume      = EXCLUDED.volume
            """), dict(sym=symbol, name=name, date=dt, o=o, h=h, l=l, c=close, v=v))
            count += 1
        except Exception:
            db.rollback()
    if count:
        db.commit()
    return count

def collect_market_history(db: Session) -> int:
    """Collect 5 years of daily OHLCV for all market symbols."""
    log.info('=== Collecting historical market data (Gold, Stocks, ETFs) ===')
    total = 0
    for symbol, ticker, name, _ in MARKET_SYMBOLS:
        result = _fetch_yahoo(ticker, period='5y', interval='1d')
        if not result:
            log.warning(f'No data for {symbol} ({ticker})')
            time.sleep(2)
            continue
        n = _save_market_rows(db, symbol, name, result)
        total += n
        log.info(f'  {symbol}: {n} days')
        time.sleep(1)
    log.info(f'=== Market history done: {total} records ===')
    return total

def collect_market_live(db: Session) -> int:
    """Collect the latest 5 trading days (called by scheduler)."""
    total = 0
    for symbol, ticker, name, _ in MARKET_SYMBOLS:
        result = _fetch_yahoo(ticker, period='5d', interval='1d')
        if not result:
            time.sleep(0.5)
            continue
        n = _save_market_rows(db, symbol, name, result)
        total += n
        time.sleep(0.5)
    log.info(f'Live market data: {total} records updated')
    return total

def collect_fear_greed(db: Session) -> int:
    """Collect Crypto Fear & Greed Index history from alternative.me (free, no key)."""
    try:
        resp = requests.get('https://api.alternative.me/fng/?limit=1826&format=json',
                            headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            log.warning(f'Fear & Greed API HTTP {resp.status_code}')
            return 0
        items = resp.json().get('data', [])
        count = 0
        for item in items:
            try:
                dt   = date.fromtimestamp(int(item['timestamp']))
                val  = int(item['value'])
                cls  = item.get('value_classification', '')
                db.execute(text("""
                    INSERT INTO fear_greed (date, value, classification)
                    VALUES (:date, :value, :cls)
                    ON CONFLICT (date) DO UPDATE SET value = EXCLUDED.value, classification = EXCLUDED.classification
                """), dict(date=dt, value=val, cls=cls))
                count += 1
            except Exception:
                pass
        db.commit()
        log.info(f'Fear & Greed Index: {count} days saved')
        return count
    except Exception as e:
        log.error(f'Fear & Greed error: {e}')
        return 0
