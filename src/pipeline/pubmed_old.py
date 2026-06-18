"""
src/pipeline/utils/pubmed.py

PubMed E-utilities helper for live literature search.

Used by:
    agent4_database  — evidence for PS1/PP5 (published case reports, P/LP replication)
    agent5_functional — evidence for PS3/BS3 (functional assay papers)

Rate limits:
    Without API key: 3 requests/second
    With API key:    10 requests/second
Set NCBI_API_KEY in .env to unlock higher rate.

NCBI E-utilities docs: https://www.ncbi.nlm.nih.gov/books/NBK25497/
"""

import logging
import os
import time
from typing import Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_NCBI_API_KEY  = os.getenv("NCBI_API_KEY", "")          # optional — set in .env
_ESEARCH_URL   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH_URL    = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_ESUMMARY_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# Conservative rate limiting regardless of key presence
_REQUEST_INTERVAL = 0.35 if _NCBI_API_KEY else 1.0   # seconds between requests
_last_request_time: float = 0.0

_TIMEOUT = 15   # seconds per request
_MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rate_limit() -> None:
    """Enforce minimum interval between NCBI requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    wait = _REQUEST_INTERVAL - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.monotonic()


def _get(url: str, params: dict) -> Optional[dict]:
    """
    GET request to NCBI with rate limiting and retry.
    Returns parsed JSON or None on failure.
    """
    if _NCBI_API_KEY:
        params["api_key"] = _NCBI_API_KEY

    for attempt in range(_MAX_RETRIES + 1):
        _rate_limit()
        try:
            resp = requests.get(url, params=params, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"PubMed rate limited — waiting {wait}s (attempt {attempt+1})")
                time.sleep(wait)
            else:
                logger.warning(f"PubMed HTTP {resp.status_code} for {url}")
                return None
        except requests.RequestException as e:
            logger.warning(f"PubMed request failed (attempt {attempt+1}): {e}")
            if attempt == _MAX_RETRIES:
                return None
            time.sleep(1)

    return None

def _build_query(
    gene: str,
    hgvsp: Optional[str] = None,
    hgvsc: Optional[str] = None,
    query_type: str = "variant",
) -> str:
    short_hgvsp = hgvsp.split(":")[-1] if hgvsp else None
    short_hgvsc = hgvsc.split(":")[-1] if hgvsc else None

    if query_type == "functional":
        parts = [f'"{gene}"[Title/Abstract]']
        if short_hgvsp:
            parts.append(f'"{short_hgvsp}"[Title/Abstract]')
        parts.append(
            '("functional" OR "assay" OR "splicing" OR "protein function" '
            'OR "cell viability" OR "HDR" OR "reporter")[Title/Abstract]'
        )
        return " AND ".join(parts)

    else:
        # Build multiple variant representations for better recall
        variant_terms = []

        if short_hgvsp:
            # p.Arg2318Ser → also try Arg2318Ser, R2318S, and 3-letter without p.
            variant_terms.append(f'"{short_hgvsp}"')   # p.Arg2318Ser

            # Strip "p." prefix
            no_p = short_hgvsp[2:] if short_hgvsp.startswith("p.") else short_hgvsp
            if no_p != short_hgvsp:
                variant_terms.append(f'"{no_p}"')       # Arg2318Ser

            # Convert 3-letter to 1-letter: Arg→R, Ser→S, etc.
            one_letter = _three_to_one_hgvsp(no_p)
            if one_letter and one_letter != no_p:
                variant_terms.append(f'"{one_letter}"')  # R2318S

        if short_hgvsc:
            variant_terms.append(f'"{short_hgvsc}"')

        if variant_terms:
            # OR across all representations, searched in title/abstract
            variant_clause = "(" + " OR ".join(
                f"{t}[Title/Abstract]" for t in variant_terms
            ) + ")"
            return f'"{gene}"[Title/Abstract] AND {variant_clause}'
        else:
            # No variant info — gene-level search only
            return f'"{gene}"[Title/Abstract] AND "variant"[Title/Abstract]'

# ---------------------------------------------------------------------------
# Amino acid 3-letter to 1-letter conversion for HGVSp normalisation
# ---------------------------------------------------------------------------

_AA3_TO_1 = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C",
    "Gln": "Q", "Glu": "E", "Gly": "G", "His": "H", "Ile": "I",
    "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F", "Pro": "P",
    "Ser": "S", "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V",
    "Ter": "*", "Sec": "U",
}


def _three_to_one_hgvsp(hgvsp_no_p: str) -> Optional[str]:
    """
    Convert 3-letter HGVSp to 1-letter: "Arg2318Ser" → "R2318S".
    Returns None if conversion fails.
    """
    import re
    m = re.match(r"([A-Z][a-z]{2})(\d+)([A-Z][a-z]{2}|\*|Ter)", hgvsp_no_p)
    if not m:
        return None
    ref_aa  = _AA3_TO_1.get(m.group(1))
    pos     = m.group(2)
    alt_aa  = _AA3_TO_1.get(m.group(3), m.group(3))
    if not ref_aa:
        return None
    return f"{ref_aa}{pos}{alt_aa}"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pubmed_search(
    gene: str,
    hgvsp: Optional[str] = None,
    hgvsc: Optional[str] = None,
    query_type: str = "variant",
    max_results: int = 10,
) -> list[dict]:
    """
    Search PubMed for papers relevant to a variant or gene functional evidence.

    Args:
        gene:        HGNC gene symbol (e.g. "BRCA2")
        hgvsp:       HGVSp notation (e.g. "NP_000050.2:p.Arg2318Ser") — optional
        hgvsc:       HGVSc notation (e.g. "NM_000059.4:c.6952A>T") — optional
        query_type:  "variant" for PS1/PP5 evidence; "functional" for PS3/BS3
        max_results: maximum papers to return (capped at 20)

    Returns:
        List of dicts: [{pmid, title, abstract, year, authors, journal}]
        Empty list on failure — callers must handle gracefully.
    """
    max_results = min(max_results, 20)

    if not gene or gene == "UNKNOWN":
        logger.debug("pubmed_search: no gene provided — skipping")
        return []

    query = _build_query(gene, hgvsp, hgvsc, query_type)
    logger.debug(f"PubMed query ({query_type}): {query}")

    # --- Step 1: esearch — get PMIDs ---
    search_params = {
        "db":       "pubmed",
        "term":     query,
        "retmax":   max_results,
        "retmode":  "json",
        "sort":     "relevance",
        "usehistory": "n",
    }
    search_data = _get(_ESEARCH_URL, search_params)
    if not search_data:
        logger.warning(f"PubMed esearch failed for gene={gene}")
        return []

    pmids = search_data.get("esearchresult", {}).get("idlist", [])
    if not pmids:
        logger.debug(f"PubMed: no results for query: {query}")
        return []

    logger.debug(f"PubMed: {len(pmids)} PMIDs for {gene} ({query_type})")

    # --- Step 2: esummary — get titles, year, journal ---
    summary_params = {
        "db":      "pubmed",
        "id":      ",".join(pmids),
        "retmode": "json",
    }
    summary_data = _get(_ESUMMARY_URL, summary_params)
    if not summary_data:
        # Return minimal results with just PMIDs
        return [{"pmid": pmid, "title": "", "abstract": "", "year": "", "authors": [], "journal": ""}
                for pmid in pmids]

    results = []
    result_map = summary_data.get("result", {})

    for pmid in pmids:
        rec = result_map.get(pmid, {})
        if not rec or pmid == "uids":
            continue

        # Extract author list (last name only for brevity)
        authors = [
            a.get("name", "") for a in rec.get("authors", [])[:3]
        ]

        results.append({
            "pmid":    pmid,
            "title":   rec.get("title", ""),
            "abstract": "",          # esummary doesn't include abstract
            "year":    rec.get("pubdate", "")[:4],
            "authors": authors,
            "journal": rec.get("source", ""),
        })

    logger.info(f"PubMed: retrieved {len(results)} records for {gene} ({query_type})")
    return results


def pubmed_format_for_llm(papers: list[dict], max_papers: int = 8) -> str:
    """
    Format PubMed results as a compact string for inclusion in LLM prompts.
    Keeps token usage low.
    """
    if not papers:
        return "  No PubMed results retrieved."

    lines = []
    for p in papers[:max_papers]:
        authors_str = ", ".join(p["authors"]) if p["authors"] else "Unknown"
        lines.append(
            f"  PMID {p['pmid']} ({p['year']}) {authors_str} — "
            f"{p['journal']}: {p['title']}"
        )
    return "\n".join(lines)
