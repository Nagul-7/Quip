"""
RAG pipeline — augments analysis with SEC filing context.
Uses LangChain + ChromaDB + sentence-transformers embeddings.
Downloads 10-K/10-Q text from SEC EDGAR on demand.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from config import get_settings
from services.sec_edgar import get_cik, get_recent_filings

_vectorstore_cache: Dict[str, Any] = {}


def _get_embedder():
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        return HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
        )
    except Exception as exc:
        logger.warning("HuggingFace embeddings unavailable: {}", exc)
        return None


def _get_vectorstore(ticker: str, persist_dir: str):
    if ticker in _vectorstore_cache:
        return _vectorstore_cache[ticker]
    try:
        import chromadb
        from langchain_community.vectorstores import Chroma
        embedder = _get_embedder()
        if embedder is None:
            return None
        ticker_dir = os.path.join(persist_dir, ticker.lower())
        os.makedirs(ticker_dir, exist_ok=True)
        vs = Chroma(
            collection_name=f"quip_{ticker.lower()}",
            embedding_function=embedder,
            persist_directory=ticker_dir,
        )
        _vectorstore_cache[ticker] = vs
        return vs
    except Exception as exc:
        logger.warning("ChromaDB init failed for {}: {}", ticker, exc)
        return None


async def _download_filing_text(cik: str, accession: str) -> Optional[str]:
    acc_clean = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{accession}-index.json"
    settings = get_settings()
    headers = {"User-Agent": settings.sec_user_agent}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(index_url, headers=headers)
            if r.status_code == 404:
                # try htm index
                index_url2 = index_url.replace("-index.json", "-index.htm")
                r = await client.get(index_url2, headers=headers)
            r.raise_for_status()
            # find the primary document
            if "json" in r.headers.get("content-type", ""):
                data = r.json()
                files = data.get("directory", {}).get("item", [])
                doc_file = next(
                    (f["name"] for f in files if f["name"].endswith(".htm") and "10-K" not in f["name"].upper()),
                    None,
                )
                if doc_file is None and files:
                    doc_file = files[0]["name"]
            else:
                return None

            if not doc_file:
                return None

            doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{doc_file}"
            doc_r = await client.get(doc_url, headers=headers)
            doc_r.raise_for_status()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(doc_r.text, "lxml")
            text = soup.get_text(separator=" ", strip=True)
            return text[:200_000]  # cap at 200k chars
    except Exception as exc:
        logger.warning("Filing download failed (cik={}, acc={}): {}", cik, accession, exc)
        return None


def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        start += chunk_size - overlap
    return chunks


async def _index_filings(ticker: str, vs) -> int:
    cik = await get_cik(ticker)
    if not cik:
        return 0
    filings = await get_recent_filings(ticker, form_type="10-K")
    if not filings:
        filings = await get_recent_filings(ticker, form_type="10-Q")
    if not filings:
        return 0

    total = 0
    for filing in filings[:2]:  # index at most 2 filings
        text = await _download_filing_text(filing["cik"], filing["accession"])
        if not text:
            continue
        chunks = _chunk_text(text)
        try:
            from langchain.schema import Document
            docs = [
                Document(
                    page_content=chunk,
                    metadata={
                        "ticker": ticker,
                        "form": filing["form"],
                        "date": filing["date"],
                        "accession": filing["accession"],
                        "chunk_id": i,
                    },
                )
                for i, chunk in enumerate(chunks)
            ]
            vs.add_documents(docs)
            total += len(docs)
            logger.info("Indexed {} chunks from {} {}", len(docs), ticker, filing["form"])
        except Exception as exc:
            logger.error("ChromaDB indexing failed: {}", exc)
    return total


_ANALYSIS_QUERIES = [
    "revenue growth and earnings trends",
    "free cash flow and capital allocation",
    "debt levels and liquidity position",
    "business risks and risk factors",
    "management discussion and analysis outlook",
    "competitive advantages and moat",
]


async def query_filings(ticker: str, questions: Optional[List[str]] = None) -> Dict[str, Any]:
    settings = get_settings()
    vs = _get_vectorstore(ticker, settings.chroma_persist_dir)
    if vs is None:
        return {"available": False, "reason": "Vector store unavailable"}

    # Check if collection is populated
    try:
        count = vs._collection.count()
    except Exception:
        count = 0

    if count == 0:
        logger.info("No chunks indexed for {} — indexing now", ticker)
        n = await _index_filings(ticker, vs)
        if n == 0:
            return {"available": False, "reason": "No SEC filings found to index"}

    queries = questions or _ANALYSIS_QUERIES
    results: Dict[str, str] = {}
    try:
        for q in queries[:4]:
            docs = vs.similarity_search(q, k=3)
            if docs:
                combined = " ".join(d.page_content for d in docs)
                results[q] = combined[:800]
    except Exception as exc:
        logger.error("RAG query failed for {}: {}", ticker, exc)
        return {"available": False, "reason": str(exc)}

    return {"available": True, "ticker": ticker, "chunks": count, "context": results}


def _summarise_rag_context(context: Dict[str, str]) -> str:
    if not context:
        return ""
    snippets = []
    for question, text in list(context.items())[:3]:
        clean = re.sub(r"\s+", " ", text)[:300]
        snippets.append(f"[{question}]: {clean}")
    return " | ".join(snippets)


async def run(ticker: str) -> Dict[str, Any]:
    logger.info("RAG pipeline: {}", ticker)
    try:
        result = await query_filings(ticker)
        if not result.get("available"):
            return {"available": False, "summary": "", "raw": result}
        summary = _summarise_rag_context(result.get("context", {}))
        return {"available": True, "summary": summary, "chunks_indexed": result.get("chunks", 0)}
    except Exception as exc:
        logger.error("RAG pipeline error for {}: {}", ticker, exc)
        return {"available": False, "summary": "", "error": str(exc)}
