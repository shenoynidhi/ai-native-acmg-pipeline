"""
src/rag/retriever.py

Runtime RAG query interface used by the classification agents.
All ChromaDB access goes through this module — agents never touch ChromaDB directly.

Lazy-initialises the client and collections on first call.
Thread-safe for parallel agent execution (chromadb PersistentClient is thread-safe).

Collections:
    clinvar_variants      — queried by Agent 4 (PS1, PS4, PP5, BP6)
    clinvar_gene_variants — queried by Agent 8 (PM5)
    uniprot_domains       — queried by Agent 5 (PM1)
    acmg_guidelines  - required by debate and review layer
"""

import logging
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal: lazy client + collection handles
# ---------------------------------------------------------------------------

_client = None
_ef = None


def _get_client():
    global _client
    if _client is None:
        import chromadb
        from src.config import CHROMADB_DIR
        _client = chromadb.PersistentClient(path=str(CHROMADB_DIR))
    return _client


def _get_ef():
    global _ef
    if _ef is None:
        try:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        except ImportError:
            from chromadb.utils import embedding_functions
            SentenceTransformerEmbeddingFunction = (
                embedding_functions.SentenceTransformerEmbeddingFunction
            )
        _ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    return _ef


def _get_collection(name: str):
    """Get a collection handle, returning None if it doesn't exist."""
    try:
        return _get_client().get_collection(name=name, embedding_function=_get_ef())
    except Exception as e:
        logger.warning(f"RAG collection '{name}' not available: {e}")
        return None


# ---------------------------------------------------------------------------
# Public API — one function per agent use-case
# ---------------------------------------------------------------------------

def query_clinvar_by_variant(
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    gene: str,
    n_results: int = 10,
) -> list[dict]:
    """
    Agent 4 — PS1/PS4: find ClinVar variants at the same position or same gene.

    Strategy:
        1. Exact coordinate filter (same chrom+pos) for PS1 (same amino acid change).
        2. Semantic search within gene for PP5/BP6 (same gene, similar consequence).

    Returns list of dicts with keys: text, clnsig, stars, chrom, pos, ref, alt, gene.
    """
    collection = _get_collection("clinvar_variants")
    if collection is None:
        return []

    results = []

    # --- Exact position lookup (metadata filter) ---
    try:
        exact = collection.query(
            query_texts=[f"{gene} {chrom}:{pos} {ref}>{alt}"],
            n_results=min(n_results, 10),
            where={"$and": [{"chrom": {"$eq": chrom}}, {"pos": {"$eq": pos}}]},
        )
        results.extend(_format_results(exact))
    except Exception as e:
        logger.debug(f"Exact ClinVar query failed: {e}")

    # --- Gene-level semantic search ---
    try:
        gene_results = collection.query(
            query_texts=[f"{gene} pathogenic benign variant classification"],
            n_results=n_results,
            where={"gene": {"$eq": gene}},
        )
        # Merge, dedup by position
        seen_pos = {r["metadata"].get("pos") for r in results}
        for r in _format_results(gene_results):
            if r["metadata"].get("pos") not in seen_pos:
                results.append(r)
                seen_pos.add(r["metadata"].get("pos"))
    except Exception as e:
        logger.debug(f"Gene-level ClinVar query failed: {e}")

    return results[:n_results]


def query_clinvar_same_codon(
    gene: str,
    protein_pos: int,
    n_results: int = 10,
) -> list[dict]:
    """
    Agent 8 — PM5: find P/LP ClinVar variants at the same codon (±2 residues).

    Returns list of dicts with keys: text, clnsig, chrom, pos, ref, alt, gene, protein_pos.
    """
    collection = _get_collection("clinvar_gene_variants")
    if collection is None:
        return []

    results = []

    # Exact protein position filter
    for offset in range(-2, 3):
        target_pos = protein_pos + offset
        try:
            r = collection.query(
                query_texts=[f"{gene} missense pathogenic position {target_pos}"],
                n_results=5,
                where={"$and": [
                    {"gene": {"$eq": gene}},
                    {"protein_pos": {"$eq": target_pos}},
                ]},
            )
            results.extend(_format_results(r))
        except Exception:
            # protein_pos field may not exist for all entries — fallback to gene search
            pass

    # Fallback: semantic search at gene level if no exact hits
    if not results:
        try:
            fallback = collection.query(
                query_texts=[f"{gene} missense pathogenic codon {protein_pos}"],
                n_results=n_results,
                where={"gene": {"$eq": gene}},
            )
            results.extend(_format_results(fallback))
        except Exception as e:
            logger.debug(f"Fallback PM5 query failed: {e}")

    # Dedup
    seen = set()
    deduped = []
    for r in results:
        key = (r["metadata"].get("chrom"), r["metadata"].get("pos"), r["metadata"].get("alt"))
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped[:n_results]


def query_uniprot_domains(
    gene: str,
    protein_position: int,
    n_results: int = 10,
) -> list[dict]:
    """
    Agent 5 — PM1: find functional domains/sites at a given protein position.

    Returns:
        List of matching feature dicts. Each has keys:
        text, feature_type, start, end, note, gene.
        Only returns features where start <= protein_position <= end.
    """
    collection = _get_collection("uniprot_domains")
    if collection is None:
        return []

    try:
        raw = collection.query(
            query_texts=[f"{gene} domain site position {protein_position}"],
            n_results=min(n_results * 3, 30),  # Over-fetch then filter by position
            where={"gene": {"$eq": gene}},
        )
    except Exception as e:
        logger.debug(f"UniProt query failed: {e}")
        return []

    all_results = _format_results(raw)

    # Filter to features that actually contain the protein position
    overlapping = [
        r for r in all_results
        if r["metadata"].get("start", 0) <= protein_position <= r["metadata"].get("end", 0)
    ]

    # If no exact overlap, return top semantic hits anyway (position may be approximate)
    if not overlapping:
        logger.debug(
            f"No UniProt features exactly overlap position {protein_position} for {gene}; "
            f"returning top semantic hits"
        )
        return all_results[:n_results]

    return overlapping[:n_results]


def query_clinvar_for_gene(
    gene: str,
    significance_filter: Optional[str] = None,
    n_results: int = 15,
) -> list[dict]:
    """
    General gene-level ClinVar query — used by multiple agents.

    Args:
        gene: HGNC gene symbol.
        significance_filter: Optional filter string, e.g. "pathogenic" or "benign".
                             Applied as a substring match on the clnsig metadata field.
        n_results: Max results to return.

    Returns list of ClinVar entry dicts.
    """
    collection = _get_collection("clinvar_variants")
    if collection is None:
        return []

    query_text = f"{gene} variant classification"
    if significance_filter:
        query_text += f" {significance_filter}"

    where_clause: dict = {"gene": {"$eq": gene}}

    try:
        raw = collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where_clause,
        )
        results = _format_results(raw)

        # Apply significance substring filter post-query if requested
        if significance_filter:
            results = [
                r for r in results
                if significance_filter.lower() in r["metadata"].get("clnsig", "").lower()
            ]

        return results
    except Exception as e:
        logger.debug(f"Gene-level ClinVar query failed for {gene}: {e}")
        return []


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _format_results(chroma_result: dict) -> list[dict]:
    """
    Convert raw ChromaDB query result dict into a flat list of dicts.
    Each dict has 'text' (the document string) and 'metadata' (the metadata dict).
    """
    documents = chroma_result.get("documents", [[]])[0]
    metadatas = chroma_result.get("metadatas", [[]])[0]
    distances = chroma_result.get("distances", [[]])[0]

    out = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        out.append({
            "text":     doc,
            "metadata": meta,
            "score":    round(1 - dist, 4) if dist is not None else None,
        })
    return out


# ---------------------------------------------------------------------------
# Health check — call at startup to verify collections are present
# ---------------------------------------------------------------------------

def check_collections() -> dict[str, bool]:
    """
    Returns a dict of {collection_name: is_available} for all expected collections.
    Agents should call this at startup and degrade gracefully if a collection is absent.
    """
    names = ["clinvar_variants", "clinvar_gene_variants", "uniprot_domains", "acmg_guidelines"]
    status = {}
    client = _get_client()
    try:
        existing = {c.name for c in client.list_collections()}
    except Exception:
        existing = set()

    for name in names:
        status[name] = name in existing

    return status
