"""
RSS/feed collector — crypto news + macro/geopolitical news that affects crypto.
Feeds: crypto sites, Reuters, MarketWatch, FT, Zero Hedge, ETF news, gold/commodity news.
"""
import feedparser, requests, logging, re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from sqlalchemy import text

log = logging.getLogger(__name__)
sia = SentimentIntensityAnalyzer()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122 Safari/537.36',
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
}

# ── All RSS feeds ──────────────────────────────────────────────────────────
RSS_FEEDS = [
    # ── Crypto-native ────────────────────────────────────────────
    {"name": "CoinDesk",       "url": "https://feeds.feedburner.com/CoinDesk",            "category": "crypto"},
    {"name": "CoinTelegraph",  "url": "https://cointelegraph.com/rss",                    "category": "crypto"},
    {"name": "Decrypt",        "url": "https://decrypt.co/feed",                          "category": "crypto"},
    {"name": "CryptoNews",     "url": "https://cryptonews.com/news/feed/",                "category": "crypto"},
    {"name": "CryptoSlate",    "url": "https://cryptoslate.com/feed/",                    "category": "crypto"},
    {"name": "NewsBTC",        "url": "https://www.newsbtc.com/feed/",                    "category": "crypto"},
    {"name": "Bitcoinist",     "url": "https://bitcoinist.com/feed/",                     "category": "crypto"},
    {"name": "AMBCrypto",      "url": "https://ambcrypto.com/feed/",                      "category": "crypto"},
    {"name": "BeInCrypto",     "url": "https://beincrypto.com/feed/",                     "category": "crypto"},
    {"name": "U.Today",        "url": "https://u.today/rss",                              "category": "crypto"},
    {"name": "TheBlock",       "url": "https://www.theblock.co/rss.xml",                  "category": "crypto"},
    {"name": "CryptoPotatoe",  "url": "https://cryptopotato.com/feed/",                   "category": "crypto"},
    {"name": "DailyCoin",      "url": "https://dailycoin.com/feed/",                      "category": "crypto"},
    {"name": "Blockworks",     "url": "https://blockworks.co/feed",                       "category": "crypto"},

    # ── ETF / Institutional ────────────────────────────────────────
    {"name": "ETF.com",        "url": "https://www.etf.com/sections/features-and-news/rss.xml", "category": "etf"},
    {"name": "ETFTrends",      "url": "https://www.etftrends.com/feed/",                  "category": "etf"},
    {"name": "CoinDesk-ETF",   "url": "https://www.coindesk.com/tag/etf/rss/",            "category": "etf"},

    # ── Macro / Global Finance ────────────────────────────────────
    {"name": "Reuters-Biz",    "url": "https://feeds.reuters.com/reuters/businessNews",   "category": "macro"},
    {"name": "Reuters-Markets", "url": "https://feeds.reuters.com/reuters/UKmarkets",     "category": "macro"},
    {"name": "Reuters-Top",    "url": "https://feeds.reuters.com/reuters/topNews",        "category": "macro"},
    {"name": "MarketWatch",    "url": "https://feeds.marketwatch.com/marketwatch/marketpulse/", "category": "macro"},
    {"name": "Bloomberg-Mkts", "url": "https://feeds.bloomberg.com/markets/news.rss",     "category": "macro"},
    {"name": "FT-Markets",     "url": "https://www.ft.com/rss/home/uk",                  "category": "macro"},
    {"name": "Investing.com",  "url": "https://www.investing.com/rss/news_301.rss",       "category": "macro"},
    {"name": "ZeroHedge",      "url": "https://feeds.feedburner.com/zerohedge/feed",      "category": "macro"},
    {"name": "SeekingAlpha",   "url": "https://seekingalpha.com/market_currents.xml",     "category": "macro"},
    {"name": "Yahoo-Finance",  "url": "https://finance.yahoo.com/news/rssindex",          "category": "macro"},
    {"name": "WSJ-Markets",    "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",   "category": "macro"},

    # ── Geopolitics / World events ────────────────────────────────
    {"name": "Reuters-World",  "url": "https://feeds.reuters.com/Reuters/worldNews",      "category": "geopolitics"},
    {"name": "Reuters-Politic","url": "https://feeds.reuters.com/Reuters/PoliticsNews",   "category": "geopolitics"},
    {"name": "AP-World",       "url": "https://feeds.apnews.com/rss/APf-WorldNews",       "category": "geopolitics"},
    {"name": "BBC-World",      "url": "http://feeds.bbci.co.uk/news/world/rss.xml",       "category": "geopolitics"},
    {"name": "BBC-Business",   "url": "http://feeds.bbci.co.uk/news/business/rss.xml",    "category": "geopolitics"},
    {"name": "Al-Jazeera",     "url": "https://www.aljazeera.com/xml/rss/all.xml",        "category": "geopolitics"},

    # ── Gold / Commodities ────────────────────────────────────────
    {"name": "Kitco-Gold",     "url": "https://www.kitco.com/rss/kitco-rss-feed.xml",     "category": "commodities"},
    {"name": "GoldPrice.org",  "url": "https://goldprice.org/feed",                       "category": "commodities"},
    {"name": "Mining.com",     "url": "https://www.mining.com/feed/",                     "category": "commodities"},
    {"name": "Proactive-Gold", "url": "https://www.proactiveinvestors.com/rss/rss_gold.rss", "category": "commodities"},

    # ── Central Banks / Monetary Policy ──────────────────────────
    {"name": "Fed-Press",      "url": "https://www.federalreserve.gov/feeds/press_all.xml","category": "monetary"},
    {"name": "ECB-News",       "url": "https://www.ecb.europa.eu/rss/enNewsAll.xml",       "category": "monetary"},
    {"name": "BIS-Research",   "url": "https://www.bis.org/rss/cbspeeches.xml",            "category": "monetary"},

    # ── CryptoPanic public ────────────────────────────────────────
    {"name": "CryptoPanic",    "url": "https://cryptopanic.com/news/rss/",                "category": "crypto"},
]

# Keywords that indicate crypto relevance for non-crypto feeds
CRYPTO_RELEVANCE_KEYWORDS = {
    'bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'blockchain', 'digital asset',
    'defi', 'nft', 'stablecoin', 'cbdc', 'binance', 'coinbase', 'tether',
    'ripple', 'solana', 'dogecoin', 'altcoin', 'web3', 'satoshi',
    # Macro signals
    'inflation', 'federal reserve', 'interest rate', 'monetary policy',
    'gold price', 'dollar index', 'dxy', 'treasury yield', 'stock market',
    'nasdaq', 's&p 500', 'recession', 'etf', 'sec', 'cftc', 'regulation',
    # Geopolitical signals
    'sanction', 'war', 'conflict', 'russia', 'ukraine', 'china economy',
    'dollar collapse', 'currency crisis', 'petrodollar',
}

CRYPTO_KEYWORDS = {
    'BTC': ['bitcoin', 'btc'],
    'ETH': ['ethereum', 'eth', 'ether'],
    'BNB': ['binance', 'bnb'],
    'SOL': ['solana', 'sol'],
    'XRP': ['ripple', 'xrp'],
    'ADA': ['cardano', 'ada'],
    'DOGE': ['dogecoin', 'doge'],
    'AVAX': ['avalanche', 'avax'],
    'DOT': ['polkadot', 'dot'],
    'MATIC': ['polygon', 'matic'],
    'LINK': ['chainlink', 'link'],
    'LTC': ['litecoin', 'ltc'],
}

def is_crypto_relevant(title: str, summary: str, category: str) -> bool:
    """For macro/geopolitics feeds, only save if crypto-relevant."""
    if category in ('crypto', 'etf'):
        return True
    text = (title + ' ' + (summary or '')).lower()
    return any(kw in text for kw in CRYPTO_RELEVANCE_KEYWORDS)

def sentiment(text: str):
    if not text:
        return 0.0, 'neutral'
    s = sia.polarity_scores(text)['compound']
    label = 'positive' if s >= 0.05 else ('negative' if s <= -0.05 else 'neutral')
    return round(s, 4), label

def find_currencies(text: str):
    t = text.lower()
    return [sym for sym, kws in CRYPTO_KEYWORDS.items() if any(k in t for k in kws)]

def parse_date(entry) -> datetime:
    for field in ('published', 'updated', 'created'):
        raw = getattr(entry, field, None) or entry.get(field)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                try:
                    return datetime.strptime(raw[:19], '%Y-%m-%dT%H:%M:%S')
                except Exception:
                    pass
    return datetime.utcnow()

def collect_rss(db) -> int:
    total = 0
    for feed_info in RSS_FEEDS:
        src_name  = feed_info["name"]
        src_url   = feed_info["url"]
        src_cat   = feed_info.get("category", "general")
        try:
            feed = feedparser.parse(src_url, request_headers=HEADERS)
            count = 0
            for entry in feed.entries[:40]:
                title   = getattr(entry, 'title', '').strip()
                link    = getattr(entry, 'link', '').strip()
                summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
                if summary:
                    summary = BeautifulSoup(summary, 'html.parser').get_text()[:800]
                if not title or not link:
                    continue

                # Skip irrelevant articles from macro/geopolitics feeds
                if not is_crypto_relevant(title, summary, src_cat):
                    continue

                pub_dt   = parse_date(entry)
                score, label = sentiment(title + ' ' + summary)
                coins    = find_currencies(title + ' ' + summary)

                try:
                    db.execute(text("""
                        INSERT INTO news_articles
                            (source, title, summary, url, published_at, sentiment_score,
                             sentiment_label, currencies, data_source, category)
                        VALUES
                            (:source, :title, :summary, :url, :pub_dt, :score,
                             :label, :coins, 'live', :category)
                        ON CONFLICT (url) DO NOTHING
                    """), dict(source=src_name, title=title[:500], summary=summary,
                               url=link[:1000], pub_dt=pub_dt, score=score, label=label,
                               coins=coins, category=src_cat))
                    db.commit()
                    count += 1
                except Exception as e:
                    db.rollback()
                    log.error(f'DB error for {src_name}: {e}')

            if count:
                log.info(f'RSS {src_name}: {count} articles')
            total += count
        except Exception as e:
            log.error(f'Feed error {src_name}: {e}')

    log.info(f'RSS total: {total} articles collected')
    return total
