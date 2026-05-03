"""
Sentiment pipeline — weight: 20%
Primary:  Groq llama-3.3-70b-versatile (structured JSON response).
Fallback: keyword-based scoring when GROQ_API_KEY is not set or call fails.

score_texts() is a lightweight keyword helper used by the /news and
/ticker-news routes for per-article badge labels — Groq is not called there.
"""
from __future__ import annotations

import asyncio
import json
from typing import Dict, List, Optional, Tuple

import httpx
from loguru import logger

from config import get_settings
from models.schemas import ComponentSignal, Signal, SentimentLabel
from services.news import fetch_ticker_news

WEIGHT = 0.20
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

_SYSTEM_PROMPT = """\
You are a financial news sentiment analyst specialising in equity markets.
Analyse the provided news headlines about a stock ticker and return ONLY a valid JSON object \
with exactly these fields:
{
  "score": <float from -1.0 (extreme bearish) to 1.0 (extreme bullish)>,
  "label": <"positive" | "negative" | "neutral">,
  "headline_summary": <one sentence (25 words max) summarising the prevailing news tone>,
  "top_headlines": [<up to 3 most market-moving headlines as raw strings>],
  "confidence": <float 0.0-1.0, lower when fewer than 3 relevant articles>
}
Scoring rules:
- score >= 0.3  -> label = "positive"
- score <= -0.3 -> label = "negative"
- otherwise     -> label = "neutral"
Return ONLY the JSON object — no markdown fences, no extra text."""


# ─── Groq API call ────────────────────────────────────────────────────────────

async def _groq_analyse(ticker: str, headlines: List[str]) -> Optional[Dict]:
    settings = get_settings()
    if not settings.groq_api_key:
        return None

    capped = headlines[:15]
    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(capped))
    user_msg = (
        f"Analyse the following {len(capped)} news headlines about {ticker.upper()}:\n\n"
        f"{numbered}"
    )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            result = json.loads(raw)
            required = {"score", "label", "headline_summary", "top_headlines", "confidence"}
            missing = required - result.keys()
            if missing:
                raise ValueError(f"Groq response missing keys: {missing}")
            result["score"]      = float(max(-1.0, min(1.0, result["score"])))
            result["confidence"] = float(max(0.0,  min(1.0, result["confidence"])))
            return result
    except Exception as exc:
        logger.warning("Groq sentiment call failed for {}: {}", ticker, exc)
        return None


# ─── Keyword fallback ─────────────────────────────────────────────────────────

_BULLISH = {
    "beat", "beats", "record", "surge", "soar", "rally", "gain", "profit",
    "growth", "strong", "upgrade", "bullish", "outperform", "raise", "raised",
    "positive", "exceed", "exceeded", "dividend", "higher", "jump", "rose", "climbs",
}
_BEARISH = {
    "miss", "misses", "drop", "fall", "plunge", "loss", "decline", "weak",
    "downgrade", "bearish", "underperform", "lower", "cut", "cuts", "slump",
    "negative", "disappoint", "disappointed", "disappointing", "lawsuit",
    "investigate", "fraud", "bankrupt", "layoff", "layoffs", "warning",
}


def _keyword_sentiment(text: str) -> Tuple[SentimentLabel, float]:
    words = set(text.lower().split())
    bull = len(words & _BULLISH)
    bear = len(words & _BEARISH)
    if bull == bear:
        return SentimentLabel.NEUTRAL, 0.0
    if bull > bear:
        return SentimentLabel.POSITIVE, min(0.5 + bull * 0.1, 0.9)
    return SentimentLabel.NEGATIVE, -min(0.5 + bear * 0.1, 0.9)


def _keyword_score_batch(texts: List[str]) -> List[Tuple[SentimentLabel, float]]:
    return [_keyword_sentiment(t) for t in texts]


# ─── score_texts — per-article helper for news card badges ───────────────────

async def score_texts(texts: List[str]) -> List[Tuple[SentimentLabel, float]]:
    """
    Lightweight keyword scoring for /news and /ticker-news badge labels.
    Runs synchronously in a thread pool — no API call, no cost.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _keyword_score_batch, texts)


# ─── Signal helper ────────────────────────────────────────────────────────────

def _signal_from_score(score_01: float) -> Signal:
    if score_01 >= 0.62:
        return Signal.BUY
    if score_01 <= 0.40:
        return Signal.SELL
    return Signal.HOLD


# ─── Main pipeline entry ──────────────────────────────────────────────────────

async def run(ticker: str) -> ComponentSignal:
    logger.info("Sentiment pipeline [Groq]: {}", ticker)
    try:
        articles = await fetch_ticker_news(ticker, page_size=12)
        if not articles:
            logger.warning("No news found for {}", ticker)
            return ComponentSignal(
                signal=Signal.HOLD,
                score=0.5,
                weight=WEIGHT,
                details={"message": "No news articles found", "article_count": 0},
            )

        headlines = [a["title"] + ". " + (a.get("description") or "") for a in articles]

        # ── Groq path ─────────────────────────────────────────────────────────
        groq_result = await _groq_analyse(ticker, headlines)
        if groq_result:
            raw_score  = groq_result["score"]          # -1.0 → 1.0
            normalised = (raw_score + 1) / 2           # 0.0 → 1.0
            confidence = groq_result["confidence"]
            # Pull toward neutral proportionally to low confidence
            final_score = 0.5 + (normalised - 0.5) * confidence

            # Annotate articles with keyword sentiment for display
            kw = _keyword_score_batch(headlines)
            for i, article in enumerate(articles):
                if i < len(kw):
                    lbl, val = kw[i]
                    article["sentiment"] = lbl.value
                    article["sentiment_score"] = round(abs(val), 4)

            return ComponentSignal(
                signal=_signal_from_score(final_score),
                score=round(final_score, 4),
                weight=WEIGHT,
                details={
                    "model": f"groq/{GROQ_MODEL}",
                    "article_count": len(articles),
                    "groq_raw_score": round(raw_score, 4),
                    "groq_label": groq_result["label"],
                    "headline_summary": groq_result.get("headline_summary", ""),
                    "top_headlines": groq_result.get("top_headlines", []),
                    "confidence": round(confidence, 4),
                    "articles": articles[:5],
                },
            )

        # ── Keyword fallback ──────────────────────────────────────────────────
        logger.info("Groq unavailable — keyword fallback for {}", ticker)
        kw = _keyword_score_batch(headlines)
        for i, article in enumerate(articles):
            if i < len(kw):
                lbl, val = kw[i]
                article["sentiment"] = lbl.value
                article["sentiment_score"] = round(abs(val), 4)

        raw_vals = [v for _, v in kw]
        avg = sum(raw_vals) / len(raw_vals) if raw_vals else 0.0
        normalised = (avg + 1) / 2
        counts = {
            "positive": sum(1 for l, _ in kw if l == SentimentLabel.POSITIVE),
            "negative": sum(1 for l, _ in kw if l == SentimentLabel.NEGATIVE),
            "neutral":  sum(1 for l, _ in kw if l == SentimentLabel.NEUTRAL),
        }
        return ComponentSignal(
            signal=_signal_from_score(normalised),
            score=round(normalised, 4),
            weight=WEIGHT,
            details={
                "model": "keyword_fallback",
                "article_count": len(articles),
                "sentiment_counts": counts,
                "articles": articles[:5],
            },
        )

    except Exception as exc:
        logger.error("Sentiment pipeline error for {}: {}", ticker, exc)
        return ComponentSignal(
            signal=Signal.HOLD, score=0.5, weight=WEIGHT, details={"error": str(exc)}
        )
