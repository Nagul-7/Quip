# Quip — AI Financial Analyst

> Quip is an AI-powered fundamental analyst built for FinoLens — it ingests SEC filings, earnings data, and market news, then commits to a BUY/SELL/HOLD call with a probability score and reasoning.

---

## What Quip Does

- **Fundamental analysis** — SEC filings (10-K/10-Q via EDGAR), free cash flow, revenue growth, earnings quality
- **News sentiment** — Groq Llama 3.3 70B processes headlines in real time (fast, free tier)
- **Deep fundamentals** — Gemini 2.5 Flash reasons over a 1M-token context window including full filing text (free tier)
- **Orchestration + anti-hallucination** — Claude Haiku 4.5 cross-checks all sub-model outputs, flags conflicts, and produces the final call
- **Outputs** — Bias label (`STRONG_BULLISH` → `STRONG_BEARISH`), probability %, plain-English reasoning, conflict detection, and a morning brief of top watchlist picks

---

## Architecture

Multi-model orchestration pipeline designed for speed and accuracy:

```
User request
     │
     ├── asyncio.gather() ─────────────────────────────────┐
     │        │                                             │
     │   Groq Llama 3.3 70B                      Gemini 2.5 Flash
     │   (news sentiment)                         (fundamentals)
     │        │                                             │
     └────────┴─────────────────────────────────────────────┘
                              │
                    Claude Haiku 4.5
              (cross-check · conflict flag · final call)
                              │
                    AnalyseResponse
              { signal, probability, reasoning, components }
```

- **Groq + Gemini run in PARALLEL** via `asyncio.gather` — never sequentially
- **Haiku** cross-checks both outputs, flags `conflict_detected`, produces the final bias
- **Redis caching** — per-ticker results cached 1 h; morning brief cached 12 h
- **APScheduler** — morning brief cron fires at 08:30 Mon–Fri, analyses all 5 watchlist tickers simultaneously
- **ChromaDB + LangChain** — RAG pipeline over SEC 10-K/10-Q filing chunks for deep context

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11, FastAPI, Uvicorn |
| **AI Orchestration** | LangChain, ChromaDB (RAG), APScheduler |
| **Cache / Queue** | Redis |
| **Models** | Groq (Llama 3.3 70B), Gemini 2.5 Flash, Claude Haiku 4.5 |
| **Financial Data** | SEC EDGAR API, Finnhub (news), Yahoo Finance (OHLCV / macro) |
| **Frontend** | Vanilla JS, HTML/CSS — FinoLens design system |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | API status + Redis connectivity check |
| `POST` | `/api/v1/analyze` | Full stock analysis (ticker extraction, pipeline, cache) |
| `GET` | `/news` | Latest financial news with FinBERT sentiment scores |
| `GET` | `/morning-brief` | Pre-cached top watchlist picks (generated at 08:30) |
| `POST` | `/combine` | Merge a Quip fundamental signal with a FinoLens technical signal |
| `GET` | `/analyse/{ticker}` | GET convenience alias for single-ticker analysis |
| `GET` | `/ticker-news/{ticker}` | Ticker-specific news with sentiment |
| `GET` | `/docs` | Interactive Swagger UI |

---

## Quick Start

```bash
git clone https://github.com/Nagul-7/Quip.git
cd Quip
cp backend/.env.example backend/.env
# Fill in your API keys in backend/.env
./start.sh
```

The script will:
1. Verify `backend/.env` exists
2. Start Redis if it isn't already running
3. Create a Python venv and install dependencies (first run only)
4. Launch the FastAPI backend at `http://localhost:8000`
5. Open the frontend (`frontend/code.html`) in your browser

---

## API Keys Required

| Key | Free? | Get it at |
|-----|-------|-----------|
| `GROQ_API_KEY` | ✅ Free | [console.groq.com](https://console.groq.com) |
| `GEMINI_API_KEY` | ✅ Free | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) |
| `ANTHROPIC_API_KEY` | 💰 ~\$1/month | [console.anthropic.com](https://console.anthropic.com) |
| `FINNHUB_API_KEY` | ✅ Free | [finnhub.io](https://finnhub.io) |

> **Note:** Quip works without `ANTHROPIC_API_KEY` — it falls back to a rule-based weighted combiner. Claude Haiku is only needed for the conflict-detection layer.

---

## Project Structure

```
Quip/
├── backend/
│   ├── main.py                  # FastAPI app + all route handlers
│   ├── config.py                # Pydantic settings (reads .env)
│   ├── requirements.txt
│   ├── .env.example
│   ├── cache/
│   │   └── redis_client.py      # Async Redis helper
│   ├── cron/
│   │   └── morning_brief.py     # APScheduler 08:30 job
│   ├── models/
│   │   └── schemas.py           # Pydantic models
│   ├── pipelines/
│   │   ├── fundamentals.py      # Gemini 2.5 Flash pipeline
│   │   ├── sentiment.py         # Groq Llama sentiment pipeline
│   │   ├── patterns.py          # Technical pattern scoring
│   │   ├── combiner.py          # Claude Haiku orchestration
│   │   └── rag.py               # ChromaDB RAG over SEC filings
│   └── services/
│       ├── news.py              # Finnhub news + static fallback
│       ├── market_data.py       # Yahoo Finance OHLCV
│       └── sec_edgar.py         # SEC EDGAR filing fetcher
└── frontend/
    └── code.html                # Single-page UI (FinoLens design)
```

---

## License

MIT — built by [Nagul](https://github.com/Nagul-7) as part of the FinoLens platform.
