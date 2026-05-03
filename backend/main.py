"""
Quip — AI-Powered Fundamental Analyst
FastAPI microservice
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from loguru import logger
from typing import Optional
from pydantic import BaseModel

from cache.redis_client import cache_get, cache_set, get_redis
from config import get_settings
from models.schemas import (
    AnalyseRequest,
    AnalyseResponse,
    CombineV2Request,
    CombineV2Response,
    HealthResponse,
    MorningBriefResponse,
    NewsResponse,
)

# ─── Logging ─────────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level=get_settings().log_level, colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")


# ─── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Quip backend starting up")
    from cron.morning_brief import setup_scheduler
    setup_scheduler(app)
    yield
    logger.info("Quip backend shutting down")
    if hasattr(app.state, "scheduler") and app.state.scheduler:
        app.state.scheduler.shutdown(wait=False)


# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Quip — AI Fundamental Analyst",
    description="Fundamental analysis microservice powering the Quip / FinoLens platform.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

settings = get_settings()

# ─── Cache key helpers ────────────────────────────────────────────────────────
def _analyse_key(ticker: str) -> str:
    return f"quip:analyse:{ticker.upper()}"

def _news_key(period: str, query: str) -> str:
    return f"news:{period}:{query.lower()[:40]}"

BRIEF_KEY = "morning_brief"  # matches cron/morning_brief.py BRIEF_CACHE_KEY

# ─── /health ──────────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    r = await get_redis()
    return HealthResponse(
        status="ok",
        redis="connected" if r else "unavailable",
    )


# ─── /news ───────────────────────────────────────────────────────────────────
@app.get("/news", response_model=NewsResponse, tags=["news"])
async def get_news(
    period: str = Query("24h", enum=["24h", "week", "month"]),
    query:  str = Query("", description="Search keywords"),
    limit:  int = Query(6, ge=1, le=20),
    force_refresh: bool = Query(False),
):
    """
    Returns financial news articles with FinBERT sentiment scores.
    Results are cached in Redis (TTL 15 min).
    """
    cache_key = _news_key(period, query)
    if not force_refresh:
        cached = await cache_get(cache_key)
        if cached:
            return NewsResponse(**cached)

    from services.news import fetch_market_news
    from pipelines.sentiment import score_texts
    from models.schemas import SentimentLabel

    articles = await fetch_market_news(period=period, query=query, page_size=limit)

    if articles:
        headlines = [a["title"] + ". " + (a.get("description") or "") for a in articles]
        scored = await score_texts(headlines)
        for i, article in enumerate(articles):
            if i < len(scored):
                lbl, val = scored[i]
                article["sentiment"] = lbl.value
                article["sentiment_score"] = round(abs(val), 4)

    response = NewsResponse(articles=articles, total=len(articles), period=period)
    await cache_set(cache_key, response.model_dump(), ttl=900)
    return response


# ─── /analyse ────────────────────────────────────────────────────────────────
@app.post("/analyse", response_model=AnalyseResponse, tags=["analysis"])
async def analyse(body: AnalyseRequest, force_refresh: bool = Query(False)):
    """
    Full Quip signal for a ticker.
    Fundamentals (Gemini) + Sentiment (Groq) run in parallel, then Claude Haiku
    orchestrates the final verdict alongside Technicals + Macro.
    Results cached for 1 hour.
    """
    ticker = body.ticker.upper().strip()
    if not ticker or len(ticker) > 5:
        raise HTTPException(status_code=422, detail="Invalid ticker symbol")

    cache_key = _analyse_key(ticker)
    if not force_refresh:
        cached = await cache_get(cache_key)
        if cached:
            try:
                r = AnalyseResponse(**cached)
                r.cached = True
                return r
            except Exception:
                pass

    import asyncio
    from pipelines import fundamentals as fund_pipeline
    from pipelines import sentiment as sent_pipeline
    from pipelines.combiner import build_analysis
    from pipelines.rag import run as run_rag

    logger.info("Running full analysis for {} (parallel AI calls)", ticker)

    # Groq (sentiment) and Gemini (fundamentals) fire at the same time — never sequentially
    fund_sig, sent_sig = await asyncio.gather(
        fund_pipeline.run(ticker),
        sent_pipeline.run(ticker),
    )

    # Claude Haiku orchestrates: runs patterns+macro then synthesises all four signals
    response = await build_analysis(ticker, fund_sig, sent_sig)

    # RAG augmentation — non-blocking, appends filing context to reasoning
    try:
        rag_result = await run_rag(ticker)
        if rag_result.get("available") and rag_result.get("summary"):
            response.reasoning += f"\n\n[Filing Context]: {rag_result['summary'][:400]}"
    except Exception as exc:
        logger.warning("RAG augmentation skipped for {}: {}", ticker, exc)

    await cache_set(cache_key, response.model_dump(), ttl=settings.cache_ttl)
    return response


# ─── /analyse/{ticker} (GET convenience alias) ───────────────────────────────
@app.get("/analyse/{ticker}", response_model=AnalyseResponse, tags=["analysis"])
async def analyse_get(ticker: str, force_refresh: bool = Query(False)):
    return await analyse(AnalyseRequest(ticker=ticker), force_refresh=force_refresh)


# ─── /morning-brief ────────────────────────────────────────────
@app.get("/morning-brief", tags=["brief"])
async def morning_brief(force_refresh: bool = Query(False)):
    """
    Returns the pre-cached morning picks sorted by bias_probability descending.
    Cache populated at 08:30 weekdays by the APScheduler cron job.
    On cache miss (or force_refresh=true), generates on demand.

    Response shape: { generated_at, picks: [{ ticker, bias, bias_probability, reasoning, confidence }], cached }
    """
    if not force_refresh:
        cached = await cache_get(BRIEF_KEY)
        if cached:
            if isinstance(cached, dict):
                # Sort picks by bias_probability desc on every read
                if "picks" in cached:
                    cached["picks"].sort(
                        key=lambda p: p.get("bias_probability", 0), reverse=True
                    )
                cached["cached"] = True
                return cached

    logger.info("Generating morning brief on demand")
    from cron.morning_brief import generate_morning_brief
    brief = await generate_morning_brief()
    return brief


# ─── /combine ────────────────────────────────────────────────────────
@app.post("/combine", response_model=CombineV2Response, tags=["combiner"])
async def combine(body: CombineV2Request):
    """
    Merges a Quip fundamental signal with a FinoLens technical signal.

    Weights: Quip 70%, FinoLens 30% (fundamental-first philosophy).
    Conflict logic:
      - If Quip is swing-horizon (short-term) vs FinoLens technical   → "swing_vs_technical"
      - If Quip is long-term fundamental vs FinoLens technical        → "fundamental_vs_technical"
    Returns final_call (BUY/SELL/HOLD), probability, and a human recommendation string.
    """
    from models.schemas import Signal

    quip = body.quip_signal
    fl   = body.finolens_signal

    # Normalise FinoLens technical bias to Signal enum
    fl_bias_raw = fl.technical_bias.upper().strip()
    try:
        fl_signal = Signal(fl_bias_raw)
    except ValueError:
        # Map non-standard labels
        if fl_bias_raw in ("LONG", "STRONG_BUY"):
            fl_signal = Signal.BUY
        elif fl_bias_raw in ("SHORT", "STRONG_SELL"):
            fl_signal = Signal.SELL
        else:
            fl_signal = Signal.HOLD

    quip_signal = quip.signal
    conflict = (quip_signal != fl_signal)

    # ── Classify conflict type ────────────────────────────────────────────
    conflict_type: Optional[str] = None
    if conflict:
        # If FinoLens has RSI / pattern data it's a technical disagreement
        has_technical_context = fl.rsi is not None or fl.pattern is not None
        if has_technical_context and quip_signal in (Signal.BUY, Signal.SELL):
            # Quip BUY vs FinoLens SELL (or vice versa) with pattern/RSI context
            conflict_type = "swing_vs_technical"
        else:
            conflict_type = "fundamental_vs_technical"

    # ── Weighted probability merge ────────────────────────────────────────
    QUIP_W  = 0.7
    FL_W    = 0.3

    def _sig_score(s: Signal) -> float:
        return {Signal.BUY: 1.0, Signal.HOLD: 0.5, Signal.SELL: 0.0}[s]

    fl_prob = fl.probability if fl.probability is not None else 0.5
    quip_num = _sig_score(quip_signal) * quip.probability * QUIP_W
    fl_num   = _sig_score(fl_signal)   * fl_prob           * FL_W
    denom    = quip.probability * QUIP_W + fl_prob * FL_W
    merged_prob = (quip_num + fl_num) / denom if denom > 0 else 0.5

    final_call = (
        Signal.BUY  if merged_prob >= 0.62 else
        Signal.SELL if merged_prob <= 0.40 else
        Signal.HOLD
    )

    # ── Build human-readable recommendation ──────────────────────────────
    quip_pct = round(quip.probability * 100, 1)
    fl_pct   = round(fl_prob * 100, 1)
    rsi_note = f", RSI {fl.rsi:.1f}" if fl.rsi is not None else ""
    pat_note = f", pattern: {fl.pattern}" if fl.pattern else ""

    if conflict:
        resolution = (
            "FinoLens technical context suggests caution — consider waiting for "
            "technical confirmation before acting on Quip's fundamental call."
            if conflict_type == "swing_vs_technical" else
            "Quip's fundamental thesis takes priority for long-horizon positions. "
            "Monitor technical levels for optimal entry."
        )
        recommendation = (
            f"CONFLICT ({conflict_type}): Quip signals {quip_signal.value} at {quip_pct}% "
            f"(fundamental) vs FinoLens {fl_signal.value} at {fl_pct}%{rsi_note}{pat_note} "
            f"(technical). Final call {final_call.value} at {round(merged_prob*100,1)}% "
            f"(Quip 70%/FinoLens 30%). {resolution}"
        )
    else:
        recommendation = (
            f"ALIGNED: Quip {quip_signal.value} ({quip_pct}%, fundamental) and FinoLens "
            f"{fl_signal.value} ({fl_pct}%{rsi_note}{pat_note}, technical) agree. "
            f"Final call {final_call.value} at {round(merged_prob*100,1)}% confidence."
        )

    return CombineV2Response(
        ticker=quip.ticker,
        final_call=final_call.value,
        probability=round(merged_prob, 4),
        quip_weight=QUIP_W,
        finolens_weight=FL_W,
        conflict=conflict,
        conflict_type=conflict_type,
        recommendation=recommendation,
        quip_bias=quip_signal.value,
        finolens_bias=fl_signal.value,
        rsi=fl.rsi,
        pattern=fl.pattern,
    )


# ─── /ticker-news/{ticker} ───────────────────────────────────────────────────
@app.get("/ticker-news/{ticker}", tags=["news"])
async def ticker_news(ticker: str, limit: int = Query(6, ge=1, le=20)):
    """Returns latest news articles specifically for a ticker with sentiment scores."""
    cache_key = f"quip:ticker_news:{ticker.upper()}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    from services.news import fetch_ticker_news
    from pipelines.sentiment import score_texts

    articles = await fetch_ticker_news(ticker.upper(), page_size=limit)
    if articles:
        headlines = [a["title"] + ". " + (a.get("description") or "") for a in articles]
        scored = await score_texts(headlines)
        for i, article in enumerate(articles):
            if i < len(scored):
                lbl, val = scored[i]
                article["sentiment"] = lbl.value
                article["sentiment_score"] = round(abs(val), 4)

    result = {"ticker": ticker.upper(), "articles": articles, "total": len(articles)}
    await cache_set(cache_key, result, ttl=900)
    return result


# ─── /api/v1/analyze  (frontend-facing alias) ────────────────────────────────
class AnalyzeRequest(BaseModel):
    ticker: Optional[str] = None
    query:  Optional[str] = None
    include_news: bool = False
    include_fundamentals: bool = True


class AnalyzeResult(BaseModel):
    ticker:           Optional[str] = None
    signal:           Optional[str] = None
    probability:      Optional[float] = None
    reasoning:        Optional[str] = None
    sentiment_label:  Optional[str] = None
    sentiment_score:  Optional[float] = None
    conflict_detected: bool = False
    error:            Optional[str] = None


@app.post("/api/v1/analyze", response_model=AnalyzeResult, tags=["analysis"])
async def api_analyze(body: AnalyzeRequest):
    """
    Frontend chat input endpoint.
    If ticker is provided, runs the full Quip pipeline and returns structured results.
    If only query is provided (no recognisable ticker), returns a reasoning-only response.
    """
    ticker = (body.ticker or "").upper().strip()

    if not ticker:
        # Query-only mode — no ticker to analyse, return a helpful message
        return AnalyzeResult(
            reasoning=(
                f"Query received: \u201c{body.query}\u201d. "
                "Please include a stock ticker (e.g. \u2018analyze NVDA\u2019) for a full fundamental analysis."
            ),
            error="No ticker symbol detected. Try: analyze NVDA"
        )

    if len(ticker) > 5:
        return AnalyzeResult(error=f"\u2018{ticker}\u2019 is not a valid ticker symbol (max 5 chars).")

    cache_key = _analyse_key(ticker)
    cached = await cache_get(cache_key)
    if cached:
        try:
            r = AnalyseResponse(**cached)
            components = r.components
            sent_comp = components.get("sentiment")
            return AnalyzeResult(
                ticker=r.ticker,
                signal=r.signal.value,
                probability=round(r.probability * 100, 1),
                reasoning=r.reasoning,
                sentiment_label=sent_comp.signal.value.lower() if sent_comp else None,
                sentiment_score=round(sent_comp.score * 100, 1) if sent_comp else None,
                conflict_detected=False,
            )
        except Exception:
            pass

    import asyncio
    from pipelines import fundamentals as fund_pipeline
    from pipelines import sentiment as sent_pipeline
    from pipelines.combiner import build_analysis

    logger.info("[api/v1/analyze] Running analysis for {}", ticker)
    try:
        fund_sig, sent_sig = await asyncio.gather(
            fund_pipeline.run(ticker),
            sent_pipeline.run(ticker),
        )
        response = await build_analysis(ticker, fund_sig, sent_sig)
    except Exception as exc:
        logger.error("[api/v1/analyze] Pipeline error for {}: {}", ticker, exc)
        return AnalyzeResult(
            ticker=ticker,
            error=f"Analysis pipeline error: {exc}",
        )

    await cache_set(cache_key, response.model_dump(), ttl=settings.cache_ttl)

    components = response.components
    sent_comp = components.get("sentiment")
    return AnalyzeResult(
        ticker=response.ticker,
        signal=response.signal.value,
        probability=round(response.probability * 100, 1),
        reasoning=response.reasoning,
        sentiment_label=sent_comp.signal.value.lower() if sent_comp else None,
        sentiment_score=round(sent_comp.score * 100, 1) if sent_comp else None,
        conflict_detected=False,
    )


# ─── Serve frontend (dev convenience) ─────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    frontend = Path(__file__).parent.parent / "frontend" / "code.html"
    if frontend.exists():
        return HTMLResponse(content=frontend.read_text(), status_code=200)
    return HTMLResponse(content="<h1>Quip backend running. Frontend not found.</h1>")
