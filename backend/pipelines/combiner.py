"""
Signal combiner — orchestrates the full Quip analysis pipeline.

build_analysis(ticker, fund_sig, sent_sig):
    Runs patterns + macro in parallel, then calls Claude Haiku to
    cross-check all four components and synthesise a final signal.
    Falls back to weighted rule-based scoring if Anthropic key is absent.

run_all_pipelines(ticker):
    Backward-compatible wrapper used by the morning-brief cron job.
    Gathers fundamentals + sentiment in parallel, then calls build_analysis.

combine_signals(quip, finolens, user_intent):
    Merges a Quip signal with a FinoLens technical signal.
    Unchanged — no AI call needed for this step.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Tuple

import httpx
from loguru import logger

from config import get_settings
from models.schemas import (
    AnalyseResponse,
    CombineResponse,
    ComponentSignal,
    FinoLensSignal,
    Signal,
)
from pipelines import fundamentals, patterns, sentiment

WEIGHTS = {
    "fundamentals": 0.40,
    "technicals":   0.30,
    "sentiment":    0.20,
    "macro":        0.10,
}

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5"

_CLAUDE_SYSTEM = """\
You are Quip, an AI financial analysis orchestrator. You receive scored signals from four \
independent sub-models for a given stock ticker and must synthesise them into a final verdict.

Return ONLY a valid JSON object with exactly these fields:
{
  "bias": "<STRONG_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONG_BEARISH | INCONCLUSIVE>",
  "bias_probability": <float 0.0-1.0>,
  "reasoning": "<2-3 sentences explaining the synthesis>",
  "confidence": <float 0.0-1.0>,
  "conflict_detected": <bool>,
  "conflict_description": <"<string>" | null>,
  "conflict_resolution": <"<string>" | null>
}

Bias mapping:
  STRONG_BULLISH  → composite score >= 0.75
  BULLISH         → composite score 0.62-0.75
  NEUTRAL         → composite score 0.42-0.62
  BEARISH         → composite score 0.25-0.42
  STRONG_BEARISH  → composite score < 0.25

Anti-hallucination rule — output INCONCLUSIVE when ANY of these are true:
  • Fundamentals and sentiment signals are strongly opposed
    (one score > 0.65 AND the other score < 0.35)
  • Any component confidence field is present and < 0.3
  • Fewer than 2 components have usable data

Return ONLY the JSON object — no markdown fences, no extra text."""


# ─── Macro score (rule-based, no API key needed) ──────────────────────────────

async def _macro_score(_ticker: str) -> ComponentSignal:
    try:
        from services.market_data import get_ohlcv
        vix_df, tny_df = await asyncio.gather(
            get_ohlcv("^VIX", period="1mo", interval="1d"),
            get_ohlcv("^TNX", period="3mo", interval="1d"),
            return_exceptions=True,
        )
        score = 0.5
        details: Dict[str, Any] = {}

        if not isinstance(vix_df, Exception) and vix_df is not None and len(vix_df) >= 5:
            vix = float(vix_df["close"].iloc[-1])
            details["vix"] = round(vix, 2)
            if vix < 15:    score += 0.12
            elif vix < 20:  score += 0.06
            elif vix > 30:  score -= 0.12
            elif vix > 25:  score -= 0.06

        if not isinstance(tny_df, Exception) and tny_df is not None and len(tny_df) >= 20:
            yield_now = float(tny_df["close"].iloc[-1])
            yield_1m  = float(tny_df["close"].iloc[-20])
            trend = yield_now - yield_1m
            details["10y_yield"]          = round(yield_now, 3)
            details["10y_yield_change_1m"] = round(trend, 3)
            if trend > 0.5:    score -= 0.08
            elif trend < -0.3: score += 0.05

        score = float(max(0.05, min(0.95, score)))
        signal = Signal.BUY if score >= 0.58 else Signal.SELL if score <= 0.42 else Signal.HOLD
        return ComponentSignal(
            signal=signal, score=round(score, 4), weight=WEIGHTS["macro"], details=details
        )
    except Exception as exc:
        logger.warning("Macro scoring failed: {}", exc)
        return ComponentSignal(
            signal=Signal.HOLD, score=0.5, weight=WEIGHTS["macro"],
            details={"error": str(exc)},
        )


# ─── Claude Haiku orchestration ───────────────────────────────────────────────

def _build_context(
    ticker: str,
    components: Dict[str, ComponentSignal],
    composite: float,
) -> str:
    fund  = components["fundamentals"]
    sent  = components["sentiment"]
    pat   = components["technicals"]
    macro = components["macro"]

    fund_ai  = fund.details.get("ai_analysis", {})
    sent_det = sent.details

    lines = [
        f"Ticker: {ticker.upper()}",
        f"Rule-based weighted composite: {composite*100:.1f}/100",
        "",
        f"── Fundamentals (weight 40%) ───────────────────",
        f"  Score:  {fund.score*100:.1f}/100  |  Signal: {fund.signal.value}",
        f"  Note:   {fund.details.get('reasoning', 'N/A')}",
    ]
    if fund_ai:
        lines += [
            f"  Gemini revenue_trend:    {fund_ai.get('revenue_trend', 'N/A')}",
            f"  Gemini earnings_summary: {fund_ai.get('earnings_summary', 'N/A')}",
            f"  Gemini valuation_note:   {fund_ai.get('valuation_note', 'N/A')}",
            f"  Gemini key_risks:        {', '.join(fund_ai.get('key_risks', [])[:3])}",
            f"  Gemini key_strengths:    {', '.join(fund_ai.get('key_strengths', [])[:3])}",
            f"  Gemini confidence:       {fund_ai.get('confidence', 'N/A')}",
        ]

    lines += [
        "",
        f"── Sentiment (weight 20%) ──────────────────────",
        f"  Score:  {sent.score*100:.1f}/100  |  Signal: {sent.signal.value}",
        f"  Model:  {sent_det.get('model', 'N/A')}",
        f"  Summary:{sent_det.get('headline_summary', 'N/A')}",
        f"  Confidence: {sent_det.get('confidence', 'N/A')}",
        f"  Top headlines:",
    ]
    for h in sent_det.get("top_headlines", [])[:3]:
        lines.append(f"    • {h}")

    lines += [
        "",
        f"── Technicals (weight 30%) ─────────────────────",
        f"  Score:  {pat.score*100:.1f}/100  |  Signal: {pat.signal.value}",
        f"  Note:   {pat.details.get('reasoning', 'N/A')}",
        "",
        f"── Macro (weight 10%) ──────────────────────────",
        f"  Score:  {macro.score*100:.1f}/100  |  Signal: {macro.signal.value}",
        f"  VIX: {macro.details.get('vix', 'N/A')}  |  "
        f"10Y yield chg: {macro.details.get('10y_yield_change_1m', 'N/A')}",
    ]
    return "\n".join(lines)


async def _claude_orchestrate(
    ticker: str,
    components: Dict[str, ComponentSignal],
    composite: float,
) -> Optional[Dict]:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None

    context = _build_context(ticker, components, composite)
    user_msg = (
        f"Synthesise the following Quip analysis for {ticker.upper()} "
        f"and return the JSON verdict:\n\n{context}"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                CLAUDE_URL,
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 512,
                    "temperature": 0.1,
                    "system": _CLAUDE_SYSTEM,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["content"][0]["text"].strip()
            # Strip markdown code fences if Claude wraps the JSON
            if raw.startswith("```"):
                raw = raw.split("```", 1)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.rsplit("```", 1)[0].strip()
            result = json.loads(raw)
            required = {
                "bias", "bias_probability", "reasoning",
                "confidence", "conflict_detected",
            }
            missing = required - result.keys()
            if missing:
                raise ValueError(f"Claude response missing keys: {missing}")
            result["bias_probability"] = float(
                max(0.0, min(1.0, result["bias_probability"]))
            )
            result["confidence"] = float(
                max(0.0, min(1.0, result["confidence"]))
            )
            return result
    except Exception as exc:
        logger.warning("Claude orchestration failed for {}: {}", ticker, exc)
        return None


def _bias_to_signal(bias: str) -> Tuple[Signal, float]:
    """Map Claude bias label to (Signal, adjusted_probability_floor)."""
    mapping = {
        "STRONG_BULLISH": (Signal.BUY,  0.80),
        "BULLISH":        (Signal.BUY,  0.65),
        "NEUTRAL":        (Signal.HOLD, 0.50),
        "BEARISH":        (Signal.SELL, 0.65),
        "STRONG_BEARISH": (Signal.SELL, 0.80),
        "INCONCLUSIVE":   (Signal.HOLD, 0.50),
    }
    return mapping.get(bias.upper(), (Signal.HOLD, 0.50))


def _build_response_from_claude(
    ticker: str,
    components: Dict[str, ComponentSignal],
    claude: Dict,
    _composite: float,
) -> AnalyseResponse:
    signal, prob_floor = _bias_to_signal(claude["bias"])
    probability = max(prob_floor, float(claude["bias_probability"]))

    # For SELL signals the probability represents conviction, not a 0→1 score
    if signal == Signal.SELL:
        probability = min(probability, 0.99)

    extra = ""
    if claude.get("conflict_detected"):
        extra = (
            f"\n\n[Conflict detected]: {claude.get('conflict_description', '')}"
            + (f" Resolution: {claude['conflict_resolution']}" if claude.get("conflict_resolution") else "")
        )

    reasoning = (
        f"[Claude Haiku — {claude['bias']}] {claude['reasoning']}{extra}"
    )

    return AnalyseResponse(
        ticker=ticker.upper(),
        signal=signal,
        probability=round(probability, 4),
        reasoning=reasoning,
        components=components,
    )


def _rule_based_fallback(
    ticker: str,
    components: Dict[str, ComponentSignal],
    composite: float,
) -> AnalyseResponse:
    signal = (
        Signal.BUY  if composite >= 0.62 else
        Signal.SELL if composite <= 0.40 else
        Signal.HOLD
    )
    fund  = components["fundamentals"]
    sent  = components["sentiment"]
    pat   = components["technicals"]
    macro = components["macro"]
    tone  = {Signal.BUY: "BULLISH", Signal.HOLD: "NEUTRAL", Signal.SELL: "BEARISH"}[signal]
    reasoning = (
        f"Quip [{tone} — rule-based fallback] {ticker.upper()} ({composite*100:.0f}%)\n"
        f"• Fundamentals {fund.signal.value} @ {fund.score*100:.0f} — "
        + str(fund.details.get("reasoning", "")) + "\n"
        f"• Sentiment    {sent.signal.value} @ {sent.score*100:.0f} — "
        + str(sent.details.get("headline_summary", f"{sent.details.get('article_count',0)} articles")) + "\n"
        f"• Technicals   {pat.signal.value} @ {pat.score*100:.0f} — "
        + str(pat.details.get("reasoning", "")) + "\n"
        f"• Macro        {macro.signal.value} @ {macro.score*100:.0f} — "
        f"VIX={macro.details.get('vix', 'N/A')}"
    )
    return AnalyseResponse(
        ticker=ticker.upper(),
        signal=signal,
        probability=round(composite, 4),
        reasoning=reasoning,
        components=components,
    )


# ─── Main entry points ────────────────────────────────────────────────────────

async def build_analysis(
    ticker: str,
    fund_sig: ComponentSignal,
    sent_sig: ComponentSignal,
) -> AnalyseResponse:
    """
    Given pre-computed fundamentals + sentiment signals, runs patterns + macro
    in parallel, then calls Claude Haiku to synthesise a final verdict.
    """
    pat_sig, macro_sig = await asyncio.gather(
        patterns.run(ticker),
        _macro_score(ticker),
    )

    components: Dict[str, ComponentSignal] = {
        "fundamentals": fund_sig,
        "sentiment":    sent_sig,
        "technicals":   pat_sig,
        "macro":        macro_sig,
    }

    composite = float(max(0.0, min(1.0,
        sum(c.score * c.weight for c in components.values())
    )))

    claude = await _claude_orchestrate(ticker, components, composite)
    if claude:
        return _build_response_from_claude(ticker, components, claude, composite)

    logger.info("Claude unavailable — rule-based fallback for {}", ticker)
    return _rule_based_fallback(ticker, components, composite)


async def run_all_pipelines(ticker: str) -> Tuple[AnalyseResponse, Dict]:
    """
    Backward-compatible wrapper used by the morning-brief cron job.
    Gathers fundamentals + sentiment in parallel (never sequentially),
    then delegates to build_analysis for patterns, macro, and Claude synthesis.
    """
    fund_sig, sent_sig = await asyncio.gather(
        fundamentals.run(ticker),
        sentiment.run(ticker),
    )
    response = await build_analysis(ticker, fund_sig, sent_sig)
    return response, dict(response.components)


# ─── Quip + FinoLens combiner (unchanged) ────────────────────────────────────

def combine_signals(
    quip: AnalyseResponse,
    finolens: FinoLensSignal,
    user_intent: Optional[str] = None,
) -> CombineResponse:
    conflict = quip.signal != finolens.signal

    if conflict and user_intent is None:
        return CombineResponse(
            ticker=quip.ticker,
            final_signal=Signal.HOLD,
            probability=0.5,
            conflict=True,
            intent_options=["swing", "long_term"],
            reasoning=(
                f"Quip signals {quip.signal.value} (fundamentals-driven, "
                f"{quip.probability*100:.0f}%) while FinoLens signals "
                f"{finolens.signal.value} (technicals-driven, "
                f"{finolens.probability*100:.0f}%). "
                "Specify intent: 'swing' (prefer technicals) or 'long_term' (prefer fundamentals)."
            ),
            quip_weight=0.5,
            finolens_weight=0.5,
        )

    if not conflict:
        quip_w, finolens_w = 0.5, 0.5
    elif user_intent == "swing":
        quip_w, finolens_w = 0.3, 0.7
    else:
        quip_w, finolens_w = 0.7, 0.3

    def sig_val(s: Signal) -> float:
        return {Signal.BUY: 1.0, Signal.HOLD: 0.5, Signal.SELL: 0.0}[s]

    combined = (
        sig_val(quip.signal) * quip.probability * quip_w
        + sig_val(finolens.signal) * finolens.probability * finolens_w
    )
    denom = quip.probability * quip_w + finolens.probability * finolens_w
    final_prob = combined / denom if denom > 0 else 0.5
    final_signal = (
        Signal.BUY  if final_prob >= 0.62 else
        Signal.SELL if final_prob <= 0.40 else
        Signal.HOLD
    )
    intent_label = f" (intent: {user_intent})" if user_intent else ""
    return CombineResponse(
        ticker=quip.ticker,
        final_signal=final_signal,
        probability=round(final_prob, 4),
        conflict=conflict,
        intent_options=None,
        reasoning=(
            f"Combined{intent_label}: Quip {quip.signal.value} ({quip_w*100:.0f}% weight) "
            f"+ FinoLens {finolens.signal.value} ({finolens_w*100:.0f}% weight) → "
            f"{final_signal.value} at {final_prob*100:.0f}% confidence."
        ),
        quip_weight=quip_w,
        finolens_weight=finolens_w,
    )
