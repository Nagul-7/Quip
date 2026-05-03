"""
SEC EDGAR API client — no API key required.
Rate limit: ~10 req/s. Enforced via tenacity backoff.
"""
import re
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_settings

BASE = "https://data.sec.gov"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

_ticker_cik_map: Dict[str, str] = {}


def _headers() -> Dict[str, str]:
    return {"User-Agent": get_settings().sec_user_agent, "Accept-Encoding": "gzip, deflate"}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _get(url: str) -> Any:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=_headers())
        r.raise_for_status()
        return r.json()


async def _load_ticker_map() -> None:
    global _ticker_cik_map
    if _ticker_cik_map:
        return
    try:
        data = await _get(TICKERS_URL)
        _ticker_cik_map = {
            v["ticker"].upper(): str(v["cik_str"]).zfill(10)
            for v in data.values()
        }
        logger.info("Loaded {} tickers from SEC EDGAR", len(_ticker_cik_map))
    except Exception as exc:
        logger.error("Failed to load SEC ticker map: {}", exc)


async def get_cik(ticker: str) -> Optional[str]:
    await _load_ticker_map()
    return _ticker_cik_map.get(ticker.upper())


async def get_company_facts(ticker: str) -> Optional[Dict[str, Any]]:
    cik = await get_cik(ticker)
    if not cik:
        logger.warning("No CIK found for ticker={}", ticker)
        return None
    try:
        url = f"{BASE}/api/xbrl/companyfacts/CIK{cik}.json"
        return await _get(url)
    except Exception as exc:
        logger.error("SEC EDGAR company facts failed for {}: {}", ticker, exc)
        return None


async def get_recent_filings(ticker: str, form_type: str = "10-K") -> List[Dict]:
    cik = await get_cik(ticker)
    if not cik:
        return []
    try:
        url = f"{BASE}/submissions/CIK{cik}.json"
        data = await _get(url)
        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        accnums = filings.get("accessionNumber", [])
        result = []
        for form, date, acc in zip(forms, dates, accnums):
            if form == form_type:
                result.append({"form": form, "date": date, "accession": acc, "cik": cik})
                if len(result) >= 3:
                    break
        return result
    except Exception as exc:
        logger.error("SEC filings fetch failed for {}: {}", ticker, exc)
        return []


def _extract_us_gaap_value(facts: Dict, concept: str, unit: str = "USD") -> Optional[float]:
    try:
        entries = facts["facts"]["us-gaap"][concept]["units"][unit]
        annual = [e for e in entries if e.get("form") in ("10-K", "10-K/A") and "end" in e]
        if not annual:
            return None
        annual.sort(key=lambda x: x["end"], reverse=True)
        return float(annual[0]["val"])
    except (KeyError, IndexError, TypeError):
        return None


async def get_key_financials(ticker: str) -> Dict[str, Optional[float]]:
    facts = await get_company_facts(ticker)
    if not facts:
        return {}
    return {
        "revenue": _extract_us_gaap_value(facts, "Revenues")
                   or _extract_us_gaap_value(facts, "RevenueFromContractWithCustomerExcludingAssessedTax"),
        "net_income": _extract_us_gaap_value(facts, "NetIncomeLoss"),
        "operating_cash_flow": _extract_us_gaap_value(facts, "NetCashProvidedByUsedInOperatingActivities"),
        "capex": _extract_us_gaap_value(facts, "PaymentsToAcquirePropertyPlantAndEquipment"),
        "total_assets": _extract_us_gaap_value(facts, "Assets"),
        "total_liabilities": _extract_us_gaap_value(facts, "Liabilities"),
        "total_equity": _extract_us_gaap_value(facts, "StockholdersEquity"),
        "current_assets": _extract_us_gaap_value(facts, "AssetsCurrent"),
        "current_liabilities": _extract_us_gaap_value(facts, "LiabilitiesCurrent"),
        "long_term_debt": _extract_us_gaap_value(facts, "LongTermDebt"),
        "shares_outstanding": _extract_us_gaap_value(facts, "CommonStockSharesOutstanding", unit="shares"),
    }
