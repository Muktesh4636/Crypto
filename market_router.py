"""
FastAPI router — macro market data endpoints.
/api/market/symbols         - list all tracked symbols
/api/market/data/{symbol}   - historical OHLCV
/api/market/latest          - latest price for all symbols
/api/market/correlations    - BTC vs Gold/SP500/DXY/VIX correlation
/api/market/fear-greed      - Crypto Fear & Greed history
/api/market/etf-flows       - Bitcoin ETF price & volume history
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from datetime import date, timedelta

router = APIRouter(prefix='/api/market', tags=['market'])

SYMBOL_META = {
    'GOLD':        {'name': 'Gold (USD/oz)',              'category': 'commodity',  'color': '#FFD700'},
    'SILVER':      {'name': 'Silver (USD/oz)',            'category': 'commodity',  'color': '#C0C0C0'},
    'OIL':         {'name': 'Crude Oil WTI (USD/bbl)',    'category': 'commodity',  'color': '#8B4513'},
    'SP500':       {'name': 'S&P 500',                   'category': 'equity',     'color': '#1E90FF'},
    'NASDAQ':      {'name': 'NASDAQ Composite',          'category': 'equity',     'color': '#00CED1'},
    'DXY':         {'name': 'US Dollar Index',           'category': 'currency',   'color': '#228B22'},
    'VIX':         {'name': 'VIX (Volatility)',          'category': 'volatility', 'color': '#DC143C'},
    'TREASURY10Y': {'name': '10Y Treasury Yield (%)',    'category': 'bonds',      'color': '#9370DB'},
    'GLD':         {'name': 'SPDR Gold ETF',             'category': 'etf',        'color': '#DAA520'},
    'IBIT':        {'name': 'iShares Bitcoin ETF (IBIT)','category': 'btc_etf',   'color': '#F7931A'},
    'FBTC':        {'name': 'Fidelity Bitcoin ETF (FBTC)','category': 'btc_etf',  'color': '#FF6B35'},
    'GBTC':        {'name': 'Grayscale Bitcoin Trust',   'category': 'btc_etf',   'color': '#6B8E23'},
    'ARKB':        {'name': 'ARK 21Shares Bitcoin ETF',  'category': 'btc_etf',   'color': '#20B2AA'},
    'BITB':        {'name': 'Bitwise Bitcoin ETF',       'category': 'btc_etf',   'color': '#FF4500'},
}

@router.get('/symbols')
def get_symbols(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT symbol, name, MIN(date) as from_date, MAX(date) as to_date, COUNT(*) as days
        FROM market_data GROUP BY symbol, name ORDER BY symbol
    """)).fetchall()
    result = []
    for r in rows:
        meta = SYMBOL_META.get(r.symbol, {})
        result.append({
            'symbol':    r.symbol,
            'name':      r.name or meta.get('name', r.symbol),
            'category':  meta.get('category', 'other'),
            'color':     meta.get('color', '#888'),
            'from_date': str(r.from_date) if r.from_date else None,
            'to_date':   str(r.to_date)   if r.to_date   else None,
            'days':      r.days,
        })
    return result

@router.get('/latest')
def get_latest(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT DISTINCT ON (symbol) symbol, name, date, close_price, open_price,
               ROUND(((close_price - open_price) / NULLIF(open_price,0) * 100)::numeric, 2) as change_pct
        FROM market_data
        ORDER BY symbol, date DESC
    """)).fetchall()
    return [dict(r._mapping) for r in rows]

@router.get('/data/{symbol}')
def get_market_data(symbol: str, days: int = 365, db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    rows = db.execute(text("""
        SELECT date, open_price, high_price, low_price, close_price, volume
        FROM market_data
        WHERE symbol = :sym AND date >= :since
        ORDER BY date ASC
    """), dict(sym=symbol.upper(), since=since)).fetchall()
    return {
        'symbol': symbol.upper(),
        'meta':   SYMBOL_META.get(symbol.upper(), {}),
        'data': [{
            'date':  str(r.date),
            'open':  r.open_price,
            'high':  r.high_price,
            'low':   r.low_price,
            'close': r.close_price,
            'volume':r.volume,
        } for r in rows]
    }

@router.get('/correlations')
def get_correlations(days: int = 365, db: Session = Depends(get_db)):
    """Return BTC daily price alongside Gold, S&P 500, NASDAQ, DXY, VIX for correlation chart."""
    since = date.today() - timedelta(days=days)
    btc = db.execute(text("""
        SELECT date, close_usd FROM price_history_daily
        WHERE symbol='BTC' AND date >= :since ORDER BY date ASC
    """), dict(since=since)).fetchall()
    btc_map = {str(r.date): r.close_usd for r in btc}

    result = {'dates': list(btc_map.keys()), 'BTC': list(btc_map.values()), 'markets': {}}

    for sym in ['GOLD', 'SP500', 'NASDAQ', 'DXY', 'VIX', 'TREASURY10Y', 'OIL', 'SILVER']:
        rows = db.execute(text("""
            SELECT date, close_price FROM market_data
            WHERE symbol=:sym AND date >= :since ORDER BY date ASC
        """), dict(sym=sym, since=since)).fetchall()
        meta = SYMBOL_META.get(sym, {})
        result['markets'][sym] = {
            'name':   meta.get('name', sym),
            'color':  meta.get('color', '#888'),
            'data':   {str(r.date): r.close_price for r in rows}
        }
    return result

@router.get('/etf-flows')
def get_etf_flows(days: int = 365, db: Session = Depends(get_db)):
    """Bitcoin ETF prices and volume over time."""
    since = date.today() - timedelta(days=days)
    etf_symbols = ['IBIT', 'FBTC', 'GBTC', 'ARKB', 'BITB', 'GLD']
    result = {}
    for sym in etf_symbols:
        rows = db.execute(text("""
            SELECT date, close_price, volume
            FROM market_data
            WHERE symbol=:sym AND date >= :since ORDER BY date ASC
        """), dict(sym=sym, since=since)).fetchall()
        if rows:
            meta = SYMBOL_META.get(sym, {})
            result[sym] = {
                'name':  meta.get('name', sym),
                'color': meta.get('color', '#888'),
                'dates':  [str(r.date) for r in rows],
                'prices': [r.close_price for r in rows],
                'volume': [r.volume for r in rows],
            }
    return result

@router.get('/fear-greed')
def get_fear_greed(days: int = 365, db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    rows = db.execute(text("""
        SELECT date, value, classification FROM fear_greed
        WHERE date >= :since ORDER BY date ASC
    """), dict(since=since)).fetchall()

    # Color each classification
    color_map = {
        'Extreme Fear': '#DC143C', 'Fear': '#FF6347',
        'Neutral': '#FFD700', 'Greed': '#90EE90', 'Extreme Greed': '#228B22'
    }
    return {
        'dates':          [str(r.date) for r in rows],
        'values':         [r.value for r in rows],
        'classifications':[r.classification for r in rows],
        'colors':         [color_map.get(r.classification, '#888') for r in rows],
        'current':        {'value': rows[-1].value, 'classification': rows[-1].classification} if rows else None,
    }

@router.get('/macro-news')
def get_macro_news(category: str = None, days: int = 30, limit: int = 50,
                   db: Session = Depends(get_db)):
    """News from macro/geopolitics/ETF/commodities categories."""
    from datetime import datetime
    since = datetime.utcnow() - timedelta(days=days)
    cats  = ['macro', 'geopolitics', 'etf', 'commodities', 'monetary', 'regulation']
    if category:
        cats = [category]

    rows = db.execute(text("""
        SELECT id, source, title, summary, url, published_at, sentiment_label,
               sentiment_score, category, currencies
        FROM news_articles
        WHERE category = ANY(:cats) AND published_at >= :since
        ORDER BY published_at DESC
        LIMIT :lim
    """), dict(cats=cats, since=since, lim=limit)).fetchall()

    return [dict(r._mapping) for r in rows]
