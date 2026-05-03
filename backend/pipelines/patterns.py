"""
Technical patterns pipeline — weight: 30%
Rule-based technical analysis with XGBoost scoring overlay.
Features: RSI, MACD, Bollinger Bands, SMA crossovers, volume trends, ATR.
XGBoost model is optional — falls back to rule-based scoring if not trained.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from models.schemas import ComponentSignal, Signal
from services.market_data import get_ohlcv

WEIGHT = 0.30
MODEL_PATH = "./data/models/xgb_patterns.json"


# ─── Technical indicators (no external TA library required) ──────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - macd_signal
    return pd.DataFrame({"macd": macd, "signal": macd_signal, "histogram": hist})


def _bollinger_bands(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    sma = close.rolling(period).mean()
    rolling_std = close.rolling(period).std()
    upper = sma + std * rolling_std
    lower = sma - std * rolling_std
    bb_pct = (close - lower) / (upper - lower + 1e-10)
    return pd.DataFrame({"upper": upper, "lower": lower, "sma": sma, "pct_b": bb_pct})


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def _compute_features(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    if len(df) < 50:
        return None

    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    rsi     = _rsi(close).iloc[-1]
    macd_df = _macd(close)
    bb      = _bollinger_bands(close)
    atr     = _atr(high, low, close)

    sma_20 = close.rolling(20).mean().iloc[-1]
    sma_50 = close.rolling(50).mean().iloc[-1]
    sma_200 = close.rolling(200).mean().iloc[-1] if len(df) >= 200 else None

    price = close.iloc[-1]
    vol_avg = vol.rolling(20).mean().iloc[-1]
    vol_ratio = vol.iloc[-1] / vol_avg if vol_avg > 0 else 1.0

    macd_cross = float(macd_df["histogram"].iloc[-1]) - float(macd_df["histogram"].iloc[-2])

    return {
        "rsi":           float(rsi) if not np.isnan(rsi) else 50.0,
        "macd_hist":     float(macd_df["histogram"].iloc[-1]),
        "macd_cross":    float(macd_cross),
        "bb_pct_b":      float(bb["pct_b"].iloc[-1]),
        "bb_width":      float((bb["upper"].iloc[-1] - bb["lower"].iloc[-1]) / bb["sma"].iloc[-1]),
        "price_vs_sma20": float(price / sma_20 - 1) if sma_20 else 0.0,
        "price_vs_sma50": float(price / sma_50 - 1) if sma_50 else 0.0,
        "price_vs_sma200":float(price / sma_200 - 1) if sma_200 else 0.0,
        "sma20_vs_sma50": float(sma_20 / sma_50 - 1) if sma_50 else 0.0,
        "volume_ratio":  float(vol_ratio),
        "atr_pct":       float(atr.iloc[-1] / price) if price else 0.0,
        "price_change_5d": float(price / close.iloc[-6] - 1) if len(close) >= 6 else 0.0,
        "price_change_20d":float(price / close.iloc[-21] - 1) if len(close) >= 21 else 0.0,
    }


# ─── Rule-based scoring ───────────────────────────────────────────────────────

def _rule_based_score(f: Dict[str, float]) -> float:
    score = 0.5
    rsi = f["rsi"]

    # RSI
    if rsi < 30:
        score += 0.15   # oversold → bullish
    elif rsi < 45:
        score += 0.07
    elif rsi > 70:
        score -= 0.15   # overbought → bearish
    elif rsi > 55:
        score -= 0.05

    # MACD crossover
    if f["macd_hist"] > 0 and f["macd_cross"] > 0:
        score += 0.10
    elif f["macd_hist"] < 0 and f["macd_cross"] < 0:
        score -= 0.10

    # Price vs moving averages
    score += np.clip(f["price_vs_sma20"] * 2, -0.08, 0.08)
    score += np.clip(f["sma20_vs_sma50"] * 3, -0.08, 0.08)
    if f["price_vs_sma200"] != 0:
        score += np.clip(f["price_vs_sma200"] * 1.5, -0.06, 0.06)

    # Bollinger band position
    bb = f["bb_pct_b"]
    if bb < 0.1:
        score += 0.08   # near lower band → oversold bounce potential
    elif bb > 0.9:
        score -= 0.08   # near upper band → extended
    elif 0.4 <= bb <= 0.6:
        score += 0.02   # middle-band momentum

    # Volume confirmation
    if f["volume_ratio"] > 1.5 and f["macd_hist"] > 0:
        score += 0.05
    elif f["volume_ratio"] > 1.5 and f["macd_hist"] < 0:
        score -= 0.05

    # Momentum
    score += np.clip(f["price_change_5d"] * 1.5, -0.06, 0.06)

    return float(np.clip(score, 0.05, 0.95))


# ─── XGBoost overlay (optional) ───────────────────────────────────────────────

_xgb_model = None


def _load_xgb():
    global _xgb_model
    if _xgb_model is not None:
        return _xgb_model
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        import xgboost as xgb
        _xgb_model = xgb.XGBClassifier()
        _xgb_model.load_model(MODEL_PATH)
        logger.info("XGBoost pattern model loaded from {}", MODEL_PATH)
        return _xgb_model
    except Exception as exc:
        logger.warning("XGBoost model load failed: {}", exc)
        return None


def _xgb_score(features: Dict[str, float]) -> Optional[float]:
    model = _load_xgb()
    if model is None:
        return None
    try:
        FEATURE_ORDER = [
            "rsi", "macd_hist", "macd_cross", "bb_pct_b", "bb_width",
            "price_vs_sma20", "price_vs_sma50", "price_vs_sma200",
            "sma20_vs_sma50", "volume_ratio", "atr_pct",
            "price_change_5d", "price_change_20d",
        ]
        X = np.array([[features.get(k, 0.0) for k in FEATURE_ORDER]])
        proba = model.predict_proba(X)[0]  # [P(SELL), P(HOLD), P(BUY)]
        # Weighted score: BUY=1, HOLD=0.5, SELL=0
        return float(proba[0] * 0.0 + proba[1] * 0.5 + proba[2] * 1.0)
    except Exception as exc:
        logger.warning("XGBoost inference failed: {}", exc)
        return None


# ─── Pipeline entry ───────────────────────────────────────────────────────────

def _signal_from_score(score: float) -> Signal:
    if score >= 0.62:
        return Signal.BUY
    if score <= 0.40:
        return Signal.SELL
    return Signal.HOLD


def _build_reasoning(features: Dict, score: float, signal: Signal, used_xgb: bool) -> str:
    rsi = features.get("rsi", 50)
    trend = "above" if features.get("price_vs_sma50", 0) > 0 else "below"
    macd_dir = "positive" if features.get("macd_hist", 0) > 0 else "negative"
    method = "XGBoost + rule-based" if used_xgb else "rule-based"
    tone = {Signal.BUY: "bullish", Signal.HOLD: "neutral", Signal.SELL: "bearish"}[signal]
    return (
        f"Technical pattern is {tone} ({method}, score {score*100:.0f}/100). "
        f"RSI={rsi:.1f}, price {trend} 50-DMA, MACD histogram {macd_dir}."
    )


async def run(ticker: str) -> ComponentSignal:
    logger.info("Patterns pipeline: {}", ticker)
    try:
        df = await get_ohlcv(ticker, period="1y", interval="1d")
        if df is None or len(df) < 30:
            return ComponentSignal(
                signal=Signal.HOLD, score=0.5, weight=WEIGHT,
                details={"message": "Insufficient price history"},
            )

        features = _compute_features(df)
        if features is None:
            return ComponentSignal(
                signal=Signal.HOLD, score=0.5, weight=WEIGHT,
                details={"message": "Feature computation failed"},
            )

        xgb_s = _xgb_score(features)
        rule_s = _rule_based_score(features)

        used_xgb = xgb_s is not None
        score = (0.6 * xgb_s + 0.4 * rule_s) if used_xgb else rule_s

        signal   = _signal_from_score(score)
        reasoning = _build_reasoning(features, score, signal, used_xgb)

        return ComponentSignal(
            signal=signal,
            score=round(score, 4),
            weight=WEIGHT,
            details={
                "features": {k: round(v, 4) for k, v in features.items()},
                "rule_score": round(rule_s, 4),
                "xgb_score": round(xgb_s, 4) if xgb_s else None,
                "method": "xgb+rules" if used_xgb else "rules",
                "reasoning": reasoning,
            },
        )
    except Exception as exc:
        logger.error("Patterns pipeline error for {}: {}", ticker, exc)
        return ComponentSignal(signal=Signal.HOLD, score=0.5, weight=WEIGHT, details={"error": str(exc)})
