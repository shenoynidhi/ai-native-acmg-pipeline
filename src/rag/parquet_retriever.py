"""
src/rag/parquet_retriever.py

DuckDB-based query interface for Parquet databases.
Provides 100-500× faster exact lookups compared to ChromaDB.

Use this for:
  - Exact coordinate lookups (chr:pos)
  - Gene symbol lookups
  - Protein position ranges
  - Constraint scores (pLI, oe_lof)

Keep ChromaDB for:
  - Semantic similarity ("find similar pathogenic variants")
  - ACMG guideline text search
  - Phenotype similarity matching

Thread-safe: DuckDB creates per-thread connections automatically.
"""

import logging
from pathlib import Path
from typing import Optional, Union
import duckdb

logger = logging.getLogger(__name__)

from src.config import DATABASE_DIR

PARQUET_DIR = DATABASE_DIR / "parquet"

# Lazy-initialized DuckDB connection (per-thread)
_conn = None


def _get_connection():
    """Get thread-local DuckDB connection (in-memory, read-only)."""
    global _conn
    if _conn is None:
        _conn = duckdb.connect(":memory:", read_only=False)
        logger.debug("[parquet_retriever] Initialized DuckDB connection")
    return _conn


# ---------------------------------------------------------------------------
# ClinVar Queries (Agent 4, Agent 8)
# ---------------------------------------------------------------------------

def query_clinvar_exact(
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    genome_build: str = "GRCh38",
) -> list[dict]:
    """
    Find exact ClinVar variant match (same chrom:pos:ref:alt).

    Args:
        chrom: Chromosome (e.g., "chr17", "17")
        pos: Position (1-based)
        ref: Reference allele
        alt: Alternate allele
        genome_build: "GRCh38" or "GRCh37"

    Returns:
        List of dicts with keys: chrom, pos, ref, alt, gene, clnsig, stars, hgvs, protein_pos
    """
    build_lower = genome_build.lower().replace("grch", "grch")
    parquet_path = PARQUET_DIR / f"clinvar_{build_lower}"

    if not parquet_path.exists():
        logger.warning(f"ClinVar Parquet not found: {parquet_path}")
        return []

    # Normalize chromosome: ClinVar stores as numbers only ("17" not "chr17")
    # Remove "chr" prefix if present
    if chrom.startswith("chr"):
        chrom = chrom[3:]  # "chr17" → "17"

    conn = _get_connection()

    try:
        query = f"""
        SELECT chrom, pos, ref, alt, gene, clnsig, stars, hgvs, protein_pos, variant_id
        FROM read_parquet('{parquet_path}/**/*.parquet')
        WHERE chrom = '{chrom}'
          AND pos = {pos}
          AND ref = '{ref}'
          AND alt = '{alt}'
        ORDER BY stars DESC
        LIMIT 10
        """
        results = conn.execute(query).fetchall()

        return [
            {
                "chrom": r[0],
                "pos": r[1],
                "ref": r[2],
                "alt": r[3],
                "gene": r[4],
                "clnsig": r[5],
                "stars": r[6],
                "hgvs": r[7],
                "protein_pos": r[8],
                "variant_id": r[9],
            }
            for r in results
        ]

    except Exception as e:
        logger.error(f"ClinVar exact query failed: {e}")
        return []


def query_clinvar_by_gene(
    gene: str,
    genome_build: str = "GRCh38",
    significance_filter: Optional[str] = None,
    min_stars: int = 2,
    limit: int = 100,
) -> list[dict]:
    """
    Find all ClinVar variants for a gene.

    Args:
        gene: Gene symbol (e.g., "BRCA1")
        genome_build: "GRCh38" or "GRCh37"
        significance_filter: Filter by CLNSIG (e.g., "Pathogenic", "Benign")
        min_stars: Minimum star rating (0-4)
        limit: Max results to return

    Returns:
        List of dicts with ClinVar variant details
    """
    build_lower = genome_build.lower().replace("grch", "grch")
    parquet_path = PARQUET_DIR / f"clinvar_{build_lower}"

    if not parquet_path.exists():
        logger.warning(f"ClinVar Parquet not found: {parquet_path}")
        return []

    conn = _get_connection()

    try:
        where_clauses = [f"gene = '{gene}'", f"stars >= {min_stars}"]

        if significance_filter:
            where_clauses.append(f"clnsig LIKE '%{significance_filter}%'")

        where_sql = " AND ".join(where_clauses)

        query = f"""
        SELECT chrom, pos, ref, alt, gene, clnsig, stars, hgvs, protein_pos, variant_id
        FROM read_parquet('{parquet_path}/**/*.parquet')
        WHERE {where_sql}
        ORDER BY stars DESC, pos ASC
        LIMIT {limit}
        """
        results = conn.execute(query).fetchall()

        return [
            {
                "chrom": r[0],
                "pos": r[1],
                "ref": r[2],
                "alt": r[3],
                "gene": r[4],
                "clnsig": r[5],
                "stars": r[6],
                "hgvs": r[7],
                "protein_pos": r[8],
                "variant_id": r[9],
            }
            for r in results
        ]

    except Exception as e:
        logger.error(f"ClinVar gene query failed: {e}")
        return []


def query_clinvar_same_codon(
    gene: str,
    protein_pos: int,
    genome_build: str = "GRCh38",
    window: int = 2,
    min_stars: int = 2,
) -> list[dict]:
    """
    Find ClinVar P/LP variants at same codon (±window residues).

    Used by Agent 8 for PM5 criterion.

    Args:
        gene: Gene symbol
        protein_pos: Protein position (e.g., 1756 for p.Gly1756)
        genome_build: "GRCh38" or "GRCh37"
        window: Search ±N residues (default 2)
        min_stars: Minimum star rating

    Returns:
        List of P/LP variants at nearby protein positions
    """
    build_lower = genome_build.lower().replace("grch", "grch")
    parquet_path = PARQUET_DIR / f"clinvar_{build_lower}"

    if not parquet_path.exists():
        logger.warning(f"ClinVar Parquet not found: {parquet_path}")
        return []

    conn = _get_connection()

    try:
        query = f"""
        SELECT chrom, pos, ref, alt, gene, clnsig, stars, hgvs, protein_pos, variant_id
        FROM read_parquet('{parquet_path}/**/*.parquet')
        WHERE gene = '{gene}'
          AND protein_pos BETWEEN {protein_pos - window} AND {protein_pos + window}
          AND protein_pos IS NOT NULL
          AND stars >= {min_stars}
          AND (clnsig LIKE '%Pathogenic%' OR clnsig LIKE '%Likely pathogenic%')
          AND clnsig NOT LIKE '%Benign%'
        ORDER BY ABS(protein_pos - {protein_pos}), stars DESC
        LIMIT 20
        """
        results = conn.execute(query).fetchall()

        return [
            {
                "chrom": r[0],
                "pos": r[1],
                "ref": r[2],
                "alt": r[3],
                "gene": r[4],
                "clnsig": r[5],
                "stars": r[6],
                "hgvs": r[7],
                "protein_pos": r[8],
                "variant_id": r[9],
            }
            for r in results
        ]

    except Exception as e:
        logger.error(f"ClinVar codon query failed: {e}")
        return []


def query_clinvar_by_variant(
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    gene: str,
    n_results: int = 15,
    genome_build: str = "GRCh38",
) -> list[dict]:
    """
    Combined ClinVar query for Agent 4 backward compatibility.

    Searches for:
      1. Exact variant match (chrom:pos:ref:alt)
      2. Same gene variants (for PS1: same amino acid change context)

    This wrapper function allows Agent 4 to use Parquet without code changes.

    Args:
        chrom: Chromosome (e.g., "17", "chr17")
        pos: Position (1-based)
        ref: Reference allele
        alt: Alternate allele
        gene: Gene symbol
        n_results: Maximum results to return
        genome_build: "GRCh38" or "GRCh37"

    Returns:
        List of dicts with ClinVar entries (exact match first, then gene-level)
    """
    # Get exact match (highest priority)
    exact_hits = query_clinvar_exact(chrom, pos, ref, alt, genome_build)

    # Get gene-level variants (for PS1 context: same AA changes)
    gene_hits = query_clinvar_by_gene(gene, limit=n_results * 2, genome_build=genome_build)

    # Merge and deduplicate by variant_id
    all_hits = exact_hits + gene_hits
    seen = set()
    unique = []

    for hit in all_hits:
        variant_id = hit.get("variant_id") or f"{hit['chrom']}:{hit['pos']}:{hit['ref']}:{hit['alt']}"
        if variant_id not in seen:
            seen.add(variant_id)
            unique.append(hit)

    # Return top n_results
    return unique[:n_results]


# ---------------------------------------------------------------------------
# gnomAD Constraint Queries (Agent 2)
# ---------------------------------------------------------------------------

def query_gnomad_constraint(gene: str) -> Optional[dict]:
    """
    Get gnomAD constraint metrics for a gene (pLI, oe_lof, etc.).

    Used by Agent 2 for PVS1 criterion (LoF intolerance).

    Args:
        gene: Gene symbol (e.g., "BRCA1")

    Returns:
        Dict with keys: gene, pLI, oe_lof, oe_lof_upper, mis_z, syn_z, lof_z
        Returns None if gene not found
    """
    parquet_path = PARQUET_DIR / "gnomad_constraint.parquet"

    if not parquet_path.exists():
        logger.warning(f"gnomAD constraint Parquet not found: {parquet_path}")
        return None

    conn = _get_connection()

    try:
        query = f"""
        SELECT gene, pLI, pNull, pRec, oe_lof, oe_lof_lower, oe_lof_upper,
               oe_mis, oe_mis_lower, oe_mis_upper, mis_z, syn_z, lof_z,
               exp_lof, obs_lof, exp_mis, obs_mis
        FROM read_parquet('{parquet_path}')
        WHERE gene = '{gene}'
        LIMIT 1
        """
        result = conn.execute(query).fetchone()

        if not result:
            return None

        return {
            "gene": result[0],
            "pLI": result[1],
            "pNull": result[2],
            "pRec": result[3],
            "oe_lof": result[4],
            "oe_lof_lower": result[5],
            "oe_lof_upper": result[6],
            "oe_mis": result[7],
            "oe_mis_lower": result[8],
            "oe_mis_upper": result[9],
            "mis_z": result[10],
            "syn_z": result[11],
            "lof_z": result[12],
            "exp_lof": result[13],
            "obs_lof": result[14],
            "exp_mis": result[15],
            "obs_mis": result[16],
        }

    except Exception as e:
        logger.error(f"gnomAD constraint query failed: {e}")
        return None


# ---------------------------------------------------------------------------
# UniProt Domain Queries (Agent 5)
# ---------------------------------------------------------------------------

def query_uniprot_domain(
    gene: str,
    protein_pos: Optional[int] = None,
    feature_type: Optional[str] = None,
) -> list[dict]:
    """
    Find UniProt protein domains for a gene.

    Used by Agent 5 for PM1 criterion (hotspot/critical domain).

    Args:
        gene: Gene symbol (e.g., "TP53")
        protein_pos: If provided, filter to domains overlapping this position
        feature_type: Filter by feature type (e.g., "Domain", "Region", "Site")

    Returns:
        List of dicts with keys: gene, feature_type, start, end, description
    """
    parquet_path = PARQUET_DIR / "uniprot_domains.parquet"

    if not parquet_path.exists():
        logger.warning(f"UniProt Parquet not found: {parquet_path}")
        return []

    conn = _get_connection()

    try:
        where_clauses = [f"gene = '{gene}'"]

        if protein_pos is not None:
            where_clauses.append(f"start <= {protein_pos} AND \"end\" >= {protein_pos}")

        if feature_type:
            where_clauses.append(f"feature_type = '{feature_type}'")

        where_sql = " AND ".join(where_clauses)

        query = f"""
        SELECT gene, feature_type, start, "end", note, protein_name
        FROM read_parquet('{parquet_path}')
        WHERE {where_sql}
        ORDER BY start ASC
        LIMIT 50
        """
        results = conn.execute(query).fetchall()

        return [
            {
                "gene": r[0],
                "feature_type": r[1],
                "start": r[2],
                "end": r[3],
                "description": r[4],  # Keep as "description" for compatibility
                "protein_name": r[5],
            }
            for r in results
        ]

    except Exception as e:
        logger.error(f"UniProt domain query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# HPO Queries (Agent 9)
# ---------------------------------------------------------------------------

def query_hpo_for_gene(gene: str, limit: int = 100) -> list[dict]:
    """
    Find HPO phenotypes associated with a gene.

    Used by Agent 9 for PP4 criterion (phenotype match).

    NOTE: HPO parquet is organized by disease, not gene. This searches
    disease_name and database_id fields for the gene symbol.

    Args:
        gene: Gene symbol (e.g., "BRCA1")
        limit: Max results to return

    Returns:
        List of dicts with keys: hpo_id, hpo_name, disease_id, disease_name
    """
    parquet_path = PARQUET_DIR / "hpo_annotations.parquet"

    if not parquet_path.exists():
        logger.warning(f"HPO Parquet not found: {parquet_path}")
        return []

    conn = _get_connection()

    try:
        # Search by disease_name or database_id containing the gene
        query = f"""
        SELECT DISTINCT hpo_id, disease_name, database_id, frequency
        FROM read_parquet('{parquet_path}')
        WHERE disease_name LIKE '%{gene}%'
           OR database_id LIKE '%{gene}%'
        ORDER BY hpo_id ASC
        LIMIT {limit}
        """
        results = conn.execute(query).fetchall()

        return [
            {
                "hpo_id": r[0],
                "hpo_name": r[1],  # Actually disease_name
                "disease_id": r[2],
                "frequency": r[3],
                "gene": gene,  # Pass through from parameter
            }
            for r in results
        ]

    except Exception as e:
        logger.error(f"HPO query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# ClinGen Queries (Agent 2)
# ---------------------------------------------------------------------------

def query_clingen_gene(gene: str) -> Optional[dict]:
    """
    Get ClinGen gene-disease validity and mechanism information.

    Used by Agent 2 for PVS1 criterion (LoF mechanism validation).

    Args:
        gene: Gene symbol (e.g., "BRCA1")

    Returns:
        Dict with keys: gene, disease, classification, mechanism, inheritance
        Returns None if gene not found
    """
    parquet_path = PARQUET_DIR / "clingen.parquet"

    if not parquet_path.exists():
        logger.warning(f"ClinGen Parquet not found: {parquet_path}")
        return None

    conn = _get_connection()

    try:
        query = f"""
        SELECT gene, disease, classification, mechanism, inheritance
        FROM read_parquet('{parquet_path}')
        WHERE gene = '{gene}'
        ORDER BY classification DESC
        LIMIT 1
        """
        result = conn.execute(query).fetchone()

        if not result:
            return None

        return {
            "gene": result[0],
            "disease": result[1],
            "classification": result[2],
            "mechanism": result[3],
            "inheritance": result[4],
        }

    except Exception as e:
        logger.error(f"ClinGen query failed: {e}")
        return None


# ---------------------------------------------------------------------------
# HGNC Queries (All Agents)
# ---------------------------------------------------------------------------

def normalize_gene_symbol(symbol: str) -> Optional[str]:
    """
    Normalize gene symbol to HGNC canonical form.

    Handles aliases and old symbols (e.g., "FAM123B" → "AMER1").

    Args:
        symbol: Gene symbol or alias

    Returns:
        Canonical HGNC symbol, or None if not found
    """
    parquet_path = PARQUET_DIR / "hgnc.parquet"

    if not parquet_path.exists():
        logger.warning(f"HGNC Parquet not found: {parquet_path}")
        return None

    conn = _get_connection()

    try:
        # Check if it's already a canonical symbol
        query = f"""
        SELECT symbol
        FROM read_parquet('{parquet_path}')
        WHERE symbol = '{symbol}'
        LIMIT 1
        """
        result = conn.execute(query).fetchone()
        if result:
            return result[0]

        # Check aliases and previous symbols
        query = f"""
        SELECT symbol
        FROM read_parquet('{parquet_path}')
        WHERE alias_symbols LIKE '%{symbol}%'
           OR prev_symbols LIKE '%{symbol}%'
        LIMIT 1
        """
        result = conn.execute(query).fetchone()

        return result[0] if result else None

    except Exception as e:
        logger.error(f"HGNC normalization failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Orphanet Queries (Agent 9)
# ---------------------------------------------------------------------------

def query_orphanet_inheritance(gene: str) -> list[str]:
    """
    Get inheritance patterns for a gene from Orphanet.

    Used by Agent 9 for phenotype/inheritance matching.

    Args:
        gene: Gene symbol (e.g., "BRCA1")

    Returns:
        List of inheritance patterns (e.g., ["AD", "AR", "XLR"])
    """
    parquet_path = PARQUET_DIR / "orphanet.parquet"

    if not parquet_path.exists():
        logger.warning(f"Orphanet Parquet not found: {parquet_path}")
        return []

    conn = _get_connection()

    try:
        query = f"""
        SELECT DISTINCT inheritance
        FROM read_parquet('{parquet_path}')
        WHERE gene = '{gene}'
          AND inheritance IS NOT NULL
        """
        results = conn.execute(query).fetchall()

        return [r[0] for r in results]

    except Exception as e:
        logger.error(f"Orphanet query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# RepeatMasker Queries (Agent 8)
# ---------------------------------------------------------------------------

def query_repeatmasker(
    chrom: str,
    start: int,
    end: int,
    repeat_class: Optional[str] = None,
) -> list[dict]:
    """
    Find repeat regions overlapping a genomic interval.

    Used by Agent 8 for PM4/BP3 criteria (repeat region context).

    Args:
        chrom: Chromosome (e.g., "chr17", "17")
        start: Start position (1-based)
        end: End position (1-based)
        repeat_class: Filter by repeat class (e.g., "SINE", "LINE", "LTR")

    Returns:
        List of dicts with keys: chrom, start, end, repeat_name, repeat_class
    """
    # Normalize chromosome: RepeatMasker stores with "chr" prefix
    if not chrom.startswith("chr"):
        chrom = f"chr{chrom}"

    parquet_path = PARQUET_DIR / "repeatmasker"

    if not parquet_path.exists():
        logger.warning(f"RepeatMasker Parquet not found: {parquet_path}")
        return []

    conn = _get_connection()

    try:
        where_clauses = [
            f"chrom = '{chrom}'",
            f"NOT (\"end\" < {start} OR start > {end})",  # Overlap condition
        ]

        if repeat_class:
            where_clauses.append(f"repeat_class = '{repeat_class}'")

        where_sql = " AND ".join(where_clauses)

        query = f"""
        SELECT chrom, start, "end", repeat_name, repeat_class, repeat_family
        FROM read_parquet('{parquet_path}/**/*.parquet')
        WHERE {where_sql}
        ORDER BY start ASC
        LIMIT 50
        """
        results = conn.execute(query).fetchall()

        return [
            {
                "chrom": r[0],
                "start": r[1],
                "end": r[2],
                "repeat_name": r[3],
                "repeat_class": r[4],
                "repeat_family": r[5],
            }
            for r in results
        ]

    except Exception as e:
        logger.error(f"RepeatMasker query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# OMIM Queries (Agent 9)
# ---------------------------------------------------------------------------

def query_omim_gene(gene: str) -> list[dict]:
    """
    Find OMIM phenotypes associated with a gene.

    Used by Agent 9 for disease/phenotype matching.

    Args:
        gene: Gene symbol (e.g., "BRCA1")

    Returns:
        List of dicts with keys: phenotype, gene_symbols, mim_number, location
    """
    parquet_path = PARQUET_DIR / "omim.parquet"

    if not parquet_path.exists():
        logger.warning(f"OMIM Parquet not found: {parquet_path}")
        return []

    conn = _get_connection()

    try:
        query = f"""
        SELECT phenotype, gene_symbols, mim_number, location
        FROM read_parquet('{parquet_path}')
        WHERE gene_symbols LIKE '%{gene}%'
        ORDER BY phenotype ASC
        LIMIT 50
        """
        results = conn.execute(query).fetchall()

        return [
            {
                "phenotype": r[0],
                "gene_symbols": r[1],
                "mim_number": r[2],
                "location": r[3],
            }
            for r in results
        ]

    except Exception as e:
        logger.error(f"OMIM query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Statistics / Debugging
# ---------------------------------------------------------------------------

def get_parquet_stats() -> dict:
    """Get statistics about available Parquet databases."""
    stats = {}

    parquet_files = {
        "clinvar_grch38": PARQUET_DIR / "clinvar_grch38",
        "clinvar_grch37": PARQUET_DIR / "clinvar_grch37",
        "gnomad_constraint": PARQUET_DIR / "gnomad_constraint.parquet",
        "uniprot_domains": PARQUET_DIR / "uniprot_domains.parquet",
        "hpo_annotations": PARQUET_DIR / "hpo_annotations.parquet",
        "clingen": PARQUET_DIR / "clingen.parquet",
        "hgnc": PARQUET_DIR / "hgnc.parquet",
        "orphanet": PARQUET_DIR / "orphanet.parquet",
        "omim": PARQUET_DIR / "omim.parquet",
        "repeatmasker": PARQUET_DIR / "repeatmasker",
    }

    conn = _get_connection()

    for name, path in parquet_files.items():
        if not path.exists():
            stats[name] = {"exists": False}
            continue

        try:
            if path.is_dir():
                query = f"SELECT COUNT(*) FROM read_parquet('{path}/**/*.parquet')"
            else:
                query = f"SELECT COUNT(*) FROM read_parquet('{path}')"

            count = conn.execute(query).fetchone()[0]
            stats[name] = {"exists": True, "row_count": count}

        except Exception as e:
            stats[name] = {"exists": True, "error": str(e)}

    return stats

