"""
Morning brief cron job — runs at 08:30 every weekday via APScheduler.

Analyses the WATCHLIST of 5 tickers with asyncio.gather() (all in parallel),
stores the top picks in Redis under key "morning_brief" with a 12-hour TTL.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from loguru import logger

from cache.redis_client import cache_set
from models.schemas import AnalyseResponse, Signal

# ─── Configuration ────────────────────────────────────────────────────────────

WATCHLIST: List[str] = ["NVDA", "AAPL", "TSLA", "MSFT", "META"]

BRIEF_CACHE_KEY = "morning_brief"       # spec: key "morning_brief"
TICKER_CACHE_PREFIX = "quip:analyse:"
BRIEF_TTL = 12 * 3600                  # 12 hours


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _bias_label(signal: Signal, prob: float) -> str:
    """Human-readable bias string derived from signal + probability."""
    if signal == Signal.BUY:
        return "STRONG_BULLISH" if prob >= 0.75 else "BULLISH"
    if signal == Signal.SELL:
        return "STRONG_BEARISH" if prob >= 0.75 else "BEARISH"
    return "NEUTRAL" if prob < 0.55 else "CAUTIOUSLY_BULLISH"


def _extract_reasoning(response: AnalyseResponse) -> str:
    """Pull a short, clean reasoning string from the analysis response."""
    if response.reasoning:
        # Strip Claude prefix tags like "[Claude Haiku — BULLISH] " if present
        text = response.reasoning
        if text.startswith("["):
            close = text.find("]")
            if close != -1:
                text = text[close + 2:]
        return text[:300].strip()
    return "No reasoning available."


def _pick_dict(response: AnalyseResponse) -> Dict:
    """Convert an AnalyseResponse into a morning-brief pick dict."""
    bias = _bias_label(response.signal, response.probability)
    sent_comp = response.components.get("sentiment")
    confidence = round(sent_comp.score, 4) if sent_comp else round(response.probability, 4)

    return {
        "ticker": response.ticker,
        "bias": bias,
        "bias_probability": round(response.probability * 100, 1),   # as percent
        "reasoning": _extract_reasoning(response),
        "confidence": round(confidence * 100, 1),                    # as percent
        "signal": response.signal.value,
        "timestamp": response.timestamp.isoformat(),
    }


# ─── Single-ticker analysis ───────────────────────────────────────────────────

async def _analyse_ticker(ticker: str) -> Optional[AnalyseResponse]:
    """Run the full Quip pipeline for one ticker. Returns None on failure."""
    try:
        from pipelines.combiner import run_all_pipelines
        response, _ = await run_all_pipelines(ticker)
        # Also warm the per-ticker analyse cache
        from cache.redis_client import cache_set as _cs
        await _cs(
            f"{TICKER_CACHE_PREFIX}{ticker.upper()}",
            response.model_dump(),
            ttl=BRIEF_TTL,
        )
        return response
    except Exception as exc:
        logger.error("Morning brief — analyse failed for {}: {}", ticker, exc)
        return None


# ─── Main generator ───────────────────────────────────────────────────────────

async def generate_morning_brief() -> Dict:
    """
    Analyse all WATCHLIST tickers in parallel with asyncio.gather(),
    sort picks by bias_probability descending, cache + return the result.
    """
    logger.info("Generating morning brief for: {}", WATCHLIST)

    # Fire all analysis tasks simultaneously — never sequentially
    raw_results = await asyncio.gather(
        *[_analyse_ticker(t) for t in WATCHLIST],
        return_exceptions=True,
    )

    successful: List[AnalyseResponse] = [
        r for r in raw_results
        if isinstance(r, AnalyseResponse)
    ]

    if not successful:
        logger.warning("Morning brief: all tickers failed — returning empty brief")

    # Sort by BUY > HOLD > SELL, then by probability
    def _sort_key(r: AnalyseResponse) -> float:
        base = {Signal.BUY: 2.0, Signal.HOLD: 1.0, Signal.SELL: 0.0}.get(r.signal, 0.0)
        return base + r.probability

    successful.sort(key=_sort_key, reverse=True)

    picks = [_pick_dict(r) for r in successful]

    # Sort picks by bias_probability descending (already sorted above, belt-and-suspenders)
    picks.sort(key=lambda p: p["bias_probability"], reverse=True)

    generated_at = datetime.now(timezone.utc).isoformat()
    brief = {
        "generated_at": generated_at,
        "picks": picks,
        "ticker_count": len(picks),
        "cached": False,
    }

    await cache_set(BRIEF_CACHE_KEY, brief, ttl=BRIEF_TTL)
    logger.info("Morning brief cached: {} picks (TTL {}h)", len(picks), BRIEF_TTL // 3600)
    return brief


# ─── APScheduler setup ────────────────────────────────────────────────────────

def setup_scheduler(app) -> None:
    """
    Attach an AsyncIOScheduler to the FastAPI app.
    Schedules generate_morning_brief() at 08:30 Mon–Fri.
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            generate_morning_brief,
            CronTrigger(day_of_week="mon-fri", hour=8, minute=30),
            id="morning_brief",
            replace_existing=True,
            misfire_grace_time=600,
        )
        scheduler.start()
        logger.info("APScheduler started — morning brief fires at 08:30 Mon-Fri")
        app.state.scheduler = scheduler
    except Exception as exc:
        logger.error("Scheduler setup failed — running without scheduled brief: {}", exc)
        app.state.scheduler = None
