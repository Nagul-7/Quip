"""
Fundamentals pipeline — weight: 40%
Step 1: Fetch ratios from yfinance + key facts from SEC EDGAR.
Step 2: Rule-based numeric scoring (always runs — reliable fallback).
Step 3: Gemini Flash 2.0 enrichment — qualitative analysis + confidence
        that scales the rule-based score toward neutral when data is thin.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

import httpx
from loguru import logger

from config import get_settings
from models.schemas import ComponentSignal, Signal
from services.market_data import get_fundamentals
from services.sec_edgar import get_key_financials

WEIGHT = 0.40

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
    "/models/gemini-2.5-flash:generateContent"
)

_GEMINI_SYSTEM = """\
You are a professional equity fundamental analyst.
Given the financial metrics below, return ONLY a valid JSON object with exactly these fields:
{
  "revenue_trend":    "<brief trend, e.g. Accelerating 20% YoY growth>",
  "earnings_summary": "<brief EPS/earnings quality note>",
  "key_risks":        ["<risk 1>", "<risk 2>", "<risk 3>"],
  "key_strengths":    ["<strength 1>", "<strength 2>", "<strength 3>"],
  "valuation_note":   "<one-sentence valuation commentary>",
  "confidence":       <float 0.0-1.0: how complete and reliable the data is>
}
Be concise and fact-based. Return ONLY the JSON object — no markdown fences, no extra text."""


# ─── Gemini API call ──────────────────────────────────────────────────────────

def _format_metrics(ticker: str, ratios: Dict, sec: Dict) -> str:
    def fmt(v: Any, pct: bool = False, bil: bool = False) -> str:
        if v is None:
            return "N/A"
        if pct:
            return f"{float(v)*100:.1f}%"
        if bil:
            return f"${float(v)/1e9:.2f}B"
        return f"{float(v):.2f}"

    lines = [
        f"Ticker: {ticker.upper()}",
        f"Sector: {ratios.get('sector', 'N/A')}  Industry: {ratios.get('industry', 'N/A')}",
        "",
        "── Valuation ──────────────────────────────",
        f"  P/E (trailing):    {fmt(ratios.get('pe_ratio'))}",
        f"  P/E (forward):     {fmt(ratios.get('forward_pe'))}",
        f"  P/B:               {fmt(ratios.get('pb_ratio'))}",
        f"  P/S:               {fmt(ratios.get('ps_ratio'))}",
        f"  PEG:               {fmt(ratios.get('peg_ratio'))}",
        f"  EV/EBITDA:         {fmt(ratios.get('ev_ebitda'))}",
        "",
        "── Growth ─────────────────────────────────",
        f"  Revenue growth YoY:  {fmt(ratios.get('revenue_growth'),  pct=True)}",
        f"  Earnings growth YoY: {fmt(ratios.get('earnings_growth'), pct=True)}",
        "",
        "── Profitability ───────────────────────────",
        f"  Profit margin:     {fmt(ratios.get('profit_margin'),    pct=True)}",
        f"  Operating margin:  {fmt(ratios.get('operating_margin'), pct=True)}",
        f"  ROE:               {fmt(ratios.get('roe'),              pct=True)}",
        f"  ROA:               {fmt(ratios.get('roa'),              pct=True)}",
        "",
        "── Balance Sheet ───────────────────────────",
        f"  Debt/Equity:       {fmt(ratios.get('debt_to_equity'))}",
        f"  Current ratio:     {fmt(ratios.get('current_ratio'))}",
        f"  Quick ratio:       {fmt(ratios.get('quick_ratio'))}",
        f"  Total cash:        {fmt(ratios.get('total_cash'),       bil=True)}",
        f"  Total debt:        {fmt(ratios.get('total_debt'),       bil=True)}",
        "",
        "── Cash Flow ───────────────────────────────",
        f"  Free cash flow:    {fmt(ratios.get('free_cashflow'),    bil=True)}",
        "",
        "── SEC EDGAR (most recent annual) ─────────",
        f"  Revenue:           {fmt(sec.get('revenue'),             bil=True)}",
        f"  Net income:        {fmt(sec.get('net_income'),          bil=True)}",
        f"  Operating CF:      {fmt(sec.get('operating_cash_flow'), bil=True)}",
        f"  Total assets:      {fmt(sec.get('total_assets'),        bil=True)}",
        f"  Total equity:      {fmt(sec.get('total_equity'),        bil=True)}",
    ]
    return "\n".join(lines)


async def _gemini_analyse(ticker: str, ratios: Dict, sec: Dict) -> Optional[Dict]:
    settings = get_settings()
    if not settings.gemini_api_key:
        return None

    context = _format_metrics(ticker, ratios, sec)
    user_msg = f"Analyse the fundamentals for {ticker.upper()}:\n\n{context}"

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                f"{GEMINI_URL}?key={settings.gemini_api_key}",
                json={
                    "system_instruction": {"parts": [{"text": _GEMINI_SYSTEM}]},
                    "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.1,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(raw)
            required = {"revenue_trend", "earnings_summary", "key_risks",
                        "key_strengths", "valuation_note", "confidence"}
            missing = required - result.keys()
            if missing:
                raise ValueError(f"Gemini response missing keys: {missing}")
            result["confidence"] = float(max(0.0, min(1.0, result["confidence"])))
            return result
    except Exception as exc:
        logger.warning("Gemini fundamentals call failed for {}: {}", ticker, exc)
        return None


# ─── Rule-based scoring ───────────────────────────────────────────────────────

def _score_pe(pe: Optional[float]) -> float:
    if pe is None: return 0.5
    if pe <= 0:    return 0.2
    if pe < 15:    return 0.9
    if pe < 25:    return 0.7
    if pe < 40:    return 0.4
    return 0.1


def _score_pb(pb: Optional[float]) -> float:
    if pb is None: return 0.5
    if pb <= 0:    return 0.2
    if pb < 1:     return 0.9
    if pb < 3:     return 0.7
    if pb < 6:     return 0.4
    return 0.2


def _score_revenue_growth(g: Optional[float]) -> float:
    if g is None:  return 0.5
    if g > 0.30:   return 0.95
    if g > 0.15:   return 0.80
    if g > 0.05:   return 0.65
    if g > 0:      return 0.55
    if g > -0.05:  return 0.40
    return 0.15


def _score_earnings_growth(g: Optional[float]) -> float:
    if g is None:  return 0.5
    if g > 0.25:   return 0.90
    if g > 0.10:   return 0.75
    if g > 0:      return 0.55
    if g > -0.10:  return 0.35
    return 0.15


def _score_roe(roe: Optional[float]) -> float:
    if roe is None: return 0.5
    if roe > 0.25:  return 0.95
    if roe > 0.15:  return 0.80
    if roe > 0.08:  return 0.60
    if roe > 0:     return 0.45
    return 0.20


def _score_debt_to_equity(de: Optional[float]) -> float:
    if de is None: return 0.5
    if de < 30:    return 0.90
    if de < 80:    return 0.70
    if de < 150:   return 0.50
    if de < 300:   return 0.30
    return 0.10


def _score_current_ratio(cr: Optional[float]) -> float:
    if cr is None:   return 0.5
    elif cr > 2.5:   return 0.80
    elif cr > 1.5:   return 0.90
    elif cr > 1.0:   return 0.65
    elif cr > 0.75:  return 0.35
    else:            return 0.10


def _score_profit_margin(pm: Optional[float]) -> float:
    if pm is None: return 0.5
    if pm > 0.25:  return 0.95
    if pm > 0.12:  return 0.80
    if pm > 0.05:  return 0.60
    if pm > 0:     return 0.45
    return 0.10


def _score_fcf(fcf: Optional[float], revenue: Optional[float]) -> float:
    if fcf is None: return 0.5
    if fcf <= 0:    return 0.15
    if revenue and revenue > 0:
        y = fcf / revenue
        if y > 0.15: return 0.95
        if y > 0.08: return 0.80
        if y > 0.03: return 0.65
        return 0.50
    return 0.60


def _compute_rule_score(ratios: Dict, sec: Dict) -> Tuple[float, Dict]:
    items = {
        "pe_ratio":        (_score_pe(ratios.get("pe_ratio")),               0.12),
        "pb_ratio":        (_score_pb(ratios.get("pb_ratio")),               0.08),
        "revenue_growth":  (_score_revenue_growth(ratios.get("revenue_growth")), 0.18),
        "earnings_growth": (_score_earnings_growth(ratios.get("earnings_growth")), 0.15),
        "roe":             (_score_roe(ratios.get("roe")),                   0.12),
        "debt_to_equity":  (_score_debt_to_equity(ratios.get("debt_to_equity")), 0.10),
        "current_ratio":   (_score_current_ratio(ratios.get("current_ratio")), 0.10),
        "profit_margin":   (_score_profit_margin(ratios.get("profit_margin")), 0.10),
        "fcf":             (_score_fcf(ratios.get("free_cashflow"), sec.get("revenue")), 0.05),
    }
    total_w = sum(w for _, w in items.values())
    composite = sum(s * w for s, w in items.values()) / total_w
    breakdown = {k: {"score": round(s, 3), "weight": w} for k, (s, w) in items.items()}
    breakdown["composite"] = round(composite, 4)
    breakdown.update({k: v for k, v in ratios.items() if v is not None})
    return composite, breakdown


def _signal_from_score(score: float) -> Signal:
    if score >= 0.62: return Signal.BUY
    if score <= 0.42: return Signal.SELL
    return Signal.HOLD


def _rule_reasoning(ratios: Dict, score: float, signal: Signal) -> str:
    parts = []
    if ratios.get("pe_ratio"):
        parts.append(f"P/E {ratios['pe_ratio']:.1f}")
    if ratios.get("revenue_growth") is not None:
        parts.append(f"rev growth {ratios['revenue_growth']*100:.1f}%")
    if ratios.get("earnings_growth") is not None:
        parts.append(f"EPS growth {ratios['earnings_growth']*100:.1f}%")
    if ratios.get("roe") is not None:
        parts.append(f"ROE {ratios['roe']*100:.1f}%")
    if ratios.get("profit_margin") is not None:
        parts.append(f"margin {ratios['profit_margin']*100:.1f}%")
    summary = ", ".join(parts) if parts else "limited data"
    tone = {Signal.BUY: "strong", Signal.HOLD: "mixed", Signal.SELL: "weak"}[signal]
    return f"Fundamentals are {tone} ({score*100:.0f}/100): {summary}."


# ─── Data fetch ───────────────────────────────────────────────────────────────

async def _fetch_data(ticker: str) -> Tuple[Dict, Dict]:
    import asyncio
    ratios, sec = await asyncio.gather(
        get_fundamentals(ticker),
        get_key_financials(ticker),
        return_exceptions=True,
    )
    return (ratios if not isinstance(ratios, Exception) else {}), \
           (sec    if not isinstance(sec,    Exception) else {})


# ─── Main pipeline entry ──────────────────────────────────────────────────────

async def run(ticker: str) -> ComponentSignal:
    logger.info("Fundamentals pipeline [Gemini]: {}", ticker)
    try:
        ratios, sec_facts = await _fetch_data(ticker)
        rule_score, details = _compute_rule_score(ratios, sec_facts)
        signal = _signal_from_score(rule_score)
        details["reasoning"] = _rule_reasoning(ratios, rule_score, signal)

        # ── Gemini enrichment ─────────────────────────────────────────────────
        gemini = await _gemini_analyse(ticker, ratios, sec_facts)
        if gemini:
            confidence = gemini["confidence"]
            # Scale rule-based score toward 0.5 when Gemini signals low confidence
            final_score = 0.5 + (rule_score - 0.5) * confidence
            signal = _signal_from_score(final_score)
            details["ai_analysis"] = {
                "model":           "gemini-2.5-flash",
                "revenue_trend":   gemini.get("revenue_trend", ""),
                "earnings_summary":gemini.get("earnings_summary", ""),
                "key_risks":       gemini.get("key_risks", []),
                "key_strengths":   gemini.get("key_strengths", []),
                "valuation_note":  gemini.get("valuation_note", ""),
                "confidence":      round(confidence, 4),
            }
            details["reasoning"] = (
                f"{gemini.get('valuation_note', '')} "
                f"Strengths: {', '.join(gemini.get('key_strengths', [])[:2])}. "
                f"Risks: {', '.join(gemini.get('key_risks', [])[:2])}."
            ).strip()
            return ComponentSignal(
                signal=signal,
                score=round(final_score, 4),
                weight=WEIGHT,
                details=details,
            )

        # ── Rule-based fallback (no Gemini key or call failed) ────────────────
        return ComponentSignal(
            signal=signal,
            score=round(rule_score, 4),
            weight=WEIGHT,
            details=details,
        )

    except Exception as exc:
        logger.error("Fundamentals pipeline error for {}: {}", ticker, exc)
        return ComponentSignal(
            signal=Signal.HOLD, score=0.5, weight=WEIGHT, details={"error": str(exc)}
        )
