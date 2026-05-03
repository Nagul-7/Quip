from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # News
    news_api_key: str = ""        # legacy NewsAPI (kept for compatibility)
    finnhub_api_key: str = ""    # https://finnhub.io — free tier, no card needed

    # Market data
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379"
    cache_ttl: int = 3600

    # SEC EDGAR
    sec_user_agent: str = "Quip/1.0 quip@finolens.io"

    # Optional
    unusual_whales_api_key: str = ""

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    # Models
    finbert_model: str = "ProsusAI/finbert"
    chroma_persist_dir: str = "./data/chroma"

    # Morning brief watchlist
    morning_brief_tickers: str = "AAPL,MSFT,NVDA,TSLA,AMZN"

    # FinoLens
    finolens_service_url: str = "http://localhost:8001"

    # AI API Keys
    groq_api_key: str = ""        # console.groq.com  — llama-3.3-70b-versatile
    gemini_api_key: str = ""      # aistudio.google.com — gemini-2.0-flash
    anthropic_api_key: str = ""   # console.anthropic.com — claude-haiku-4-5

    @property
    def watchlist(self) -> List[str]:
        return [t.strip().upper() for t in self.morning_brief_tickers.split(",") if t.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
