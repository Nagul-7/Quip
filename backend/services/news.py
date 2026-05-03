"""
News service — Finnhub primary, static hardcoded fallback.
Returns normalised NewsArticle-compatible dicts.
"""
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from config import get_settings

FINNHUB_BASE = "https://finnhub.io/api/v1"

# Static fallback articles — shown when Finnhub key is missing or API call fails
STATIC_ARTICLES: List[Dict] = [
    {
        "id": "static_001",
        "title": "Federal Reserve signals cautious approach amid persistent inflation",
        "description": (
            "Fed officials struck a measured tone at this week's FOMC meeting, "
            "keeping the benchmark rate unchanged while acknowledging that core PCE "
            "remains above the 2% target. Markets interpreted the statement as "
            "leaning hawkish, sending the 10-year Treasury yield to a six-week high."
        ),
        "url": "https://www.wsj.com/economy/central-banking",
        "image_url": None,
        "source": "Wall Street Journal",
        "published_at": "2025-05-01T08:00:00+00:00",
        "published_at_formatted": "MAY 01, 2025",
        "sentiment": "neutral",
        "sentiment_score": 0.12,
        "tickers": ["SPY", "TLT"],
    },
    {
        "id": "static_002",
        "title": "NVIDIA blowout earnings reignite AI infrastructure spending thesis",
        "description": (
            "NVDA reported Q1 revenue of $26 billion, up 78% year-over-year, "
            "as hyperscalers accelerated data-centre buildout. Data-centre segment "
            "alone grew 427% annually. Analysts revised 12-month price targets "
            "upward, with the consensus now sitting above $1,100 per share."
        ),
        "url": "https://finance.yahoo.com/news/nvidia-earnings",
        "image_url": None,
        "source": "Yahoo Finance",
        "published_at": "2025-04-30T21:00:00+00:00",
        "published_at_formatted": "APR 30, 2025",
        "sentiment": "positive",
        "sentiment_score": 0.91,
        "tickers": ["NVDA", "AMD", "SMCI"],
    },
    {
        "id": "static_003",
        "title": "Oil retreats as OPEC+ production-cut deal shows early cracks",
        "description": (
            "Brent crude slipped below $82 per barrel after satellite imagery "
            "and tanker-tracking data suggested two OPEC+ members were pumping "
            "above quota. Energy stocks gave up early gains as analysts warned "
            "a supply overhang could weigh on prices through Q3."
        ),
        "url": "https://www.bloomberg.com/energy",
        "image_url": None,
        "source": "Bloomberg",
        "published_at": "2025-04-29T14:30:00+00:00",
        "published_at_formatted": "APR 29, 2025",
        "sentiment": "negative",
        "sentiment_score": 0.67,
        "tickers": ["XLE", "USO", "CVX"],
    },
]


def _article_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%b %d, %Y").upper()


async def fetch_market_news(period: str = "24h", query: str = "", page_size: int = 9) -> List[Dict]:
    """Primary: Finnhub /news?category=general. Fallback: 3 static articles."""
    settings = get_settings()
    if settings.finnhub_api_key:
        try:
            articles = await _fetch_finnhub(settings.finnhub_api_key, query, page_size)
            if articles:
                return articles
            logger.warning("Finnhub returned 0 articles — using static fallback")
        except Exception as exc:
            logger.error("Finnhub fetch failed: {} — using static fallback", exc)
    else:
        logger.info("No FINNHUB_API_KEY configured — using static fallback")
    return _static_fallback(page_size)


async def fetch_ticker_news(ticker: str, page_size: int = 6) -> List[Dict]:
    """Fetch news for a specific ticker via Finnhub company-news endpoint."""
    settings = get_settings()
    if settings.finnhub_api_key:
        try:
            articles = await _fetch_finnhub_ticker(settings.finnhub_api_key, ticker, page_size)
            if articles:
                return articles
        except Exception as exc:
            logger.error("Finnhub ticker news failed for {}: {}", ticker, exc)
    return _static_fallback(min(page_size, 3))


async def _fetch_finnhub(api_key: str, query: str, page_size: int) -> List[Dict]:
    """GET /news?category=general from Finnhub (free tier)."""
    params: Dict[str, Any] = {
        "category": "general",
        "token": api_key,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{FINNHUB_BASE}/news", params=params)
        r.raise_for_status()
        raw = r.json()  # list of articles

    if not isinstance(raw, list):
        logger.warning("Finnhub /news returned unexpected shape: {}", type(raw))
        return []

    articles = []
    for item in raw[:page_size]:
        ts = item.get("datetime", 0)
        try:
            pub_dt = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else datetime.now(timezone.utc)
        except Exception:
            pub_dt = datetime.now(timezone.utc)

        headline = item.get("headline") or ""
        summary = item.get("summary") or headline

        articles.append({
            "id": _article_id(item.get("url") or str(ts)),
            "title": headline,
            "description": summary,
            "published_at": pub_dt.isoformat(),
            "published_at_formatted": _fmt_date(pub_dt),
            "url": item.get("url") or "#",
            "image_url": item.get("image") or None,
            "source": item.get("source") or "Finnhub",
            "sentiment": "neutral",
            "sentiment_score": 0.0,
            "tickers": _extract_tickers(headline + " " + summary),
        })
    return articles


async def _fetch_finnhub_ticker(api_key: str, ticker: str, page_size: int) -> List[Dict]:
    """GET /company-news?symbol=TICKER from Finnhub."""
    from datetime import timedelta
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=7)
    params: Dict[str, Any] = {
        "symbol": ticker.upper(),
        "from": from_dt.strftime("%Y-%m-%d"),
        "to": to_dt.strftime("%Y-%m-%d"),
        "token": api_key,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{FINNHUB_BASE}/company-news", params=params)
        r.raise_for_status()
        raw = r.json()

    if not isinstance(raw, list):
        return []

    articles = []
    for item in raw[:page_size]:
        ts = item.get("datetime", 0)
        try:
            pub_dt = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else datetime.now(timezone.utc)
        except Exception:
            pub_dt = datetime.now(timezone.utc)

        headline = item.get("headline") or ""
        summary = item.get("summary") or headline

        articles.append({
            "id": _article_id(item.get("url") or str(ts)),
            "title": headline,
            "description": summary,
            "published_at": pub_dt.isoformat(),
            "published_at_formatted": _fmt_date(pub_dt),
            "url": item.get("url") or "#",
            "image_url": item.get("image") or None,
            "source": item.get("source") or "Finnhub",
            "sentiment": "neutral",
            "sentiment_score": 0.0,
            "tickers": [ticker.upper()] + _extract_tickers(headline + " " + summary),
        })
    return articles


def _static_fallback(limit: int) -> List[Dict]:
    """Return static hardcoded articles — used when API is unavailable."""
    return STATIC_ARTICLES[:max(1, limit)]


_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
_COMMON_WORDS = {
    "I", "A", "THE", "AND", "OR", "IS", "IT", "IN", "AT", "OF", "TO",
    "US", "UK", "EU", "AI", "CEO", "CFO", "IPO", "ETF", "SEC", "GDP",
    "SP", "USD", "EUR", "Q1", "Q2", "Q3", "Q4", "FED", "ECB", "IMF",
    "FOR", "ON", "AS", "BY", "BE", "AN", "NO", "IF", "UP",
}


def _extract_tickers(text: str) -> List[str]:
    found = _TICKER_RE.findall(text)
    return list({t for t in found if t not in _COMMON_WORDS and len(t) >= 2})[:5]
