"""
Market data service — Yahoo Finance primary, Alpaca secondary.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd
import yfinance as yf
from loguru import logger

from config import get_settings


def _yf_ticker(ticker: str) -> yf.Ticker:
    return yf.Ticker(ticker.upper())


async def get_ohlcv(ticker: str, period: str = "6mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    try:
        t = _yf_ticker(ticker)
        df = t.history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            logger.warning("Empty OHLCV for {}", ticker)
            return None
        df.index = df.index.tz_localize(None)
        return df[["Open", "High", "Low", "Close", "Volume"]].rename(
            columns=str.lower
        )
    except Exception as exc:
        logger.error("OHLCV fetch failed for {}: {}", ticker, exc)
        return None


async def get_quote(ticker: str) -> Dict[str, Any]:
    try:
        t = _yf_ticker(ticker)
        info = t.fast_info
        return {
            "ticker": ticker.upper(),
            "price": float(info.last_price or 0),
            "market_cap": float(info.market_cap or 0),
            "volume": float(info.three_month_average_volume or 0),
            "52w_high": float(info.fifty_two_week_high or 0),
            "52w_low": float(info.fifty_two_week_low or 0),
        }
    except Exception as exc:
        logger.error("Quote fetch failed for {}: {}", ticker, exc)
        return {"ticker": ticker.upper()}


async def get_fundamentals(ticker: str) -> Dict[str, Any]:
    """Return valuation and profitability metrics from yfinance."""
    try:
        t = _yf_ticker(ticker)
        info = t.info
        return {
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "pb_ratio": info.get("priceToBook"),
            "ps_ratio": info.get("priceToSalesTrailing12Months"),
            "peg_ratio": info.get("pegRatio"),
            "ev_ebitda": info.get("enterpriseToEbitda"),
            "roe": info.get("returnOnEquity"),
            "roa": info.get("returnOnAssets"),
            "profit_margin": info.get("profitMargins"),
            "operating_margin": info.get("operatingMargins"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "free_cashflow": info.get("freeCashflow"),
            "total_debt": info.get("totalDebt"),
            "total_cash": info.get("totalCash"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "quick_ratio": info.get("quickRatio"),
            "beta": info.get("beta"),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
        }
    except Exception as exc:
        logger.error("Fundamentals fetch failed for {}: {}", ticker, exc)
        return {}


async def get_earnings_history(ticker: str) -> Dict[str, Any]:
    try:
        t = _yf_ticker(ticker)
        earnings = t.earnings_history
        if earnings is None or earnings.empty:
            return {}
        recent = earnings.tail(4).to_dict(orient="records")
        return {"recent_quarters": recent}
    except Exception as exc:
        logger.warning("Earnings history failed for {}: {}", ticker, exc)
        return {}
