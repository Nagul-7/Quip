from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


# ─── News ────────────────────────────────────────────────────────────────────

class NewsArticle(BaseModel):
    id: str
    title: str
    description: str
    published_at: datetime
    published_at_formatted: str
    url: str
    image_url: Optional[str] = None
    source: str
    sentiment: SentimentLabel = SentimentLabel.NEUTRAL
    sentiment_score: float = 0.0
    tickers: List[str] = []


class NewsResponse(BaseModel):
    articles: List[NewsArticle]
    total: int
    period: str


# ─── Analysis ────────────────────────────────────────────────────────────────

class ComponentSignal(BaseModel):
    signal: Signal
    score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0)
    details: Dict[str, Any] = {}


class AnalyseRequest(BaseModel):
    ticker: str

    @property
    def symbol(self) -> str:
        return self.ticker.upper().strip()


class AnalyseResponse(BaseModel):
    ticker: str
    signal: Signal
    probability: float = Field(ge=0.0, le=1.0)
    reasoning: str
    components: Dict[str, ComponentSignal]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    cached: bool = False


# ─── Morning Brief ───────────────────────────────────────────────────────────

class MorningPickItem(BaseModel):
    ticker: str
    signal: Signal
    probability: float
    bias: str
    summary: str
    timestamp: datetime


class MorningBriefResponse(BaseModel):
    picks: List[MorningPickItem]
    generated_at: datetime
    cached: bool = False


# ─── Combiner ────────────────────────────────────────────────────────────────

class FinoLensSignal(BaseModel):
    ticker: str
    signal: Signal
    probability: float
    reasoning: str


class CombineRequest(BaseModel):
    ticker: str
    quip_signal: AnalyseResponse
    finolens_signal: FinoLensSignal
    user_intent: Optional[str] = None  # "swing" | "long_term"


class CombineResponse(BaseModel):
    ticker: str
    final_signal: Signal
    probability: float
    conflict: bool
    intent_options: Optional[List[str]] = None
    reasoning: str
    quip_weight: float
    finolens_weight: float



class FinoLensTechnicalSignal(BaseModel):
    """Flat technical signal shape sent by the FinoLens frontend / service."""
    ticker: str
    technical_bias: str          # "BUY" | "SELL" | "HOLD"
    rsi: Optional[float] = None  # 0–100
    pattern: Optional[str] = None  # e.g. "Double Bottom", "Head & Shoulders"
    probability: Optional[float] = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: Optional[str] = None


class CombineV2Request(BaseModel):
    """Request body for POST /combine (v2)."""
    quip_signal: AnalyseResponse
    finolens_signal: FinoLensTechnicalSignal
    # Optional — if provided, resolves conflict without asking again
    user_intent: Optional[str] = None   # "swing" | "long_term"


class CombineV2Response(BaseModel):
    """Response from POST /combine (v2)."""
    ticker: str
    final_call: str                      # BUY | SELL | HOLD
    probability: float
    quip_weight: float = 0.7
    finolens_weight: float = 0.3
    conflict: bool
    conflict_type: Optional[str] = None  # "swing_vs_technical" | "fundamental_vs_technical"
    recommendation: str
    quip_bias: str
    finolens_bias: str
    rsi: Optional[float] = None
    pattern: Optional[str] = None


# ─── Health ──────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    redis: str
    version: str = "1.0.0"
