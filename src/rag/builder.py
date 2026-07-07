"""
src/rag/builder.py

One-time offline script that builds all ChromaDB collections used by the agents.
Run this ONCE after downloading databases, before running the pipeline.

Collections built:
  1. clinvar_variants        — ClinVar ≥2-star variants (Agent 4: PS1, PS4, PP5, BP6)
  2. clinvar_gene_variants   — ClinVar P/LP missense by gene+position (Agent 8: PM5)
  3. uniprot_domains         — UniProt protein domains/sites (Agent 5: PM1)

Usage:
    conda activate acmg
    cd /home/ubuntu/ai-native-acmg-pipeline/
    python -m src.rag.builder

Takes ~20-60 min on first run depending on ClinVar size.
"""

# CRITICAL: Ensure environment is loaded BEFORE sentence-transformers import
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment early (before ChromaDB/sentence-transformers import)
env_aws = Path(__file__).parent.parent.parent / ".env.aws"
env_default = Path(__file__).parent.parent.parent / ".env"
if env_aws.exists():
    load_dotenv(env_aws)
else:
    load_dotenv(env_default)

# Set cache directories from environment
SENTENCE_TRANSFORMERS_HOME = os.getenv("SENTENCE_TRANSFORMERS_HOME")
TRANSFORMERS_CACHE = os.getenv("TRANSFORMERS_CACHE")
if SENTENCE_TRANSFORMERS_HOME:
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = SENTENCE_TRANSFORMERS_HOME
if TRANSFORMERS_CACHE:
    os.environ["TRANSFORMERS_CACHE"] = TRANSFORMERS_CACHE

import logging
import sys
import re

import cyvcf2

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ChromaDB client + embedding setup
# ---------------------------------------------------------------------------

def _get_client():
    """Return a persistent ChromaDB client pointed at CHROMADB_DIR."""
    from src.config import CHROMADB_DIR
    from src.rag.chromadb_client import get_chromadb_client
    return get_chromadb_client(CHROMADB_DIR)


def _get_ef():
    """
    Return the sentence-transformers embedding function.
    Handles both old (chromadb < 0.5) and new (chromadb >= 0.5) import paths.
    """
    try:
        # chromadb >= 0.5
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError:
        # chromadb 0.4.x
        from chromadb.utils import embedding_functions
        SentenceTransformerEmbeddingFunction = (
            embedding_functions.SentenceTransformerEmbeddingFunction
        )
    return SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")


# ---------------------------------------------------------------------------
# Helper: ClinVar CLNREVSTAT → star count
# ---------------------------------------------------------------------------

def _revstat_to_stars(revstat: str) -> int:
    revstat = revstat.lower().replace(" ", "_")
    if "practice_guideline" in revstat:
        return 4
    if "reviewed_by_expert_panel" in revstat:
        return 3
    if "criteria_provided" in revstat and "multiple_submitters" in revstat and "no_conflicts" in revstat:
        return 2
    if "criteria_provided" in revstat and "single_submitter" in revstat:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Collection 1 — clinvar_variants  (≥2 star, all significance classes)
# Build-specific collections: clinvar_variants_grch38 and clinvar_variants_grch37
# ---------------------------------------------------------------------------

def build_clinvar_collection(genome_build: str = "GRCh38"):
    """
    Index ClinVar ≥2-star variants into ChromaDB for a specific genome build.
    Each document encodes gene, coordinates, significance, disease, and stars.
    Used by Agent 4 for PS1, PS4, PP5, BP6.

    Args:
        genome_build: "GRCh38" or "GRCh37"

    Returns:
        Number of variants indexed
    """
    from src.config import get_database_paths

    clinvar_vcf = str(get_database_paths(genome_build)["clinvar_vcf"])

    # Build-specific collection name
    build_suffix = genome_build.lower().replace("grch", "grch")  # grch38 or grch37
    collection_name = f"clinvar_variants_{build_suffix}"

    logger.info(f"Building {collection_name} from {clinvar_vcf}")

    client = _get_client()
    ef = _get_ef()

    # Drop and recreate so re-runs are idempotent
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    collection = client.create_collection(
        name=collection_name,
        embedding_function=ef,
        metadata={
            "description": f"ClinVar >=2 star variants for Agent 4 ({genome_build})",
            "genome_build": genome_build
        },
    )

    vcf = cyvcf2.VCF(clinvar_vcf)
    batch_docs, batch_metas, batch_ids = [], [], []
    batch_size = 500
    count = 0
    skipped = 0

    for variant in vcf:
        clnrevstat = variant.INFO.get("CLNREVSTAT", "") or ""
        stars = _revstat_to_stars(clnrevstat)
        if stars < 2:
            skipped += 1
            continue

        clnsig  = variant.INFO.get("CLNSIG",  "") or ""
        clndn   = variant.INFO.get("CLNDN",   "") or ""
        geneinfo = variant.INFO.get("GENEINFO", "") or ""
        gene = geneinfo.split(":")[0] if geneinfo else ""
        alt  = variant.ALT[0] if variant.ALT else "."
        vid  = f"clinvar_{variant.ID or count}"

        doc_text = (
            f"Gene:{gene} "
            f"Variant:{variant.CHROM}:{variant.POS}:{variant.REF}:{alt} "
            f"Significance:{clnsig} "
            f"Disease:{clndn} "
            f"Stars:{stars}"
        )

        batch_docs.append(doc_text)
        batch_metas.append({
            "gene":   gene,
            "clnsig": clnsig,
            "clndn":  clndn,  # Disease name for PS1/PM5 disease matching
            "stars":  stars,
            "chrom":  variant.CHROM,
            "pos":    int(variant.POS),
            "ref":    variant.REF,
            "alt":    alt,
        })
        batch_ids.append(vid)
        count += 1

        if len(batch_docs) >= batch_size:
            collection.add(
                documents=batch_docs,
                metadatas=batch_metas,
                ids=batch_ids,
            )
            batch_docs, batch_metas, batch_ids = [], [], []
            if count % 5000 == 0:
                logger.info(f"  clinvar_variants: {count} indexed, {skipped} skipped (<2 stars)...")

    if batch_docs:
        collection.add(documents=batch_docs, metadatas=batch_metas, ids=batch_ids)

    vcf.close()
    logger.info(f"clinvar_variants: DONE — {count} variants indexed, {skipped} skipped")
    return count


# ---------------------------------------------------------------------------
# Collection 2 — clinvar_gene_variants  (P/LP missense only, for Agent 8 PM5)
# Build-specific collections: clinvar_gene_variants_grch38 and clinvar_gene_variants_grch37
# ---------------------------------------------------------------------------

def build_clinvar_gene_collection(genome_build: str = "GRCh38"):
    """
    Index ClinVar Pathogenic/Likely_Pathogenic missense variants, grouped by gene.
    Used by Agent 8 for PM5: different missense at same codon previously classified P/LP.

    Args:
        genome_build: "GRCh38" or "GRCh37"

    Returns:
        Number of variants indexed
    """
    from src.config import get_database_paths

    clinvar_vcf = str(get_database_paths(genome_build)["clinvar_vcf"])

    # Build-specific collection name
    build_suffix = genome_build.lower().replace("grch", "grch")  # grch38 or grch37
    collection_name = f"clinvar_gene_variants_{build_suffix}"

    logger.info(f"Building {collection_name} from {clinvar_vcf}")

    client = _get_client()
    ef = _get_ef()

    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    collection = client.create_collection(
        name=collection_name,
        embedding_function=ef,
        metadata={
            "description": f"ClinVar P/LP missense variants by gene for Agent 8 PM5 ({genome_build})",
            "genome_build": genome_build
        },
    )

    # Only include variants matching these significance terms
    PATHOGENIC_TERMS = {"pathogenic", "likely_pathogenic"}

    vcf = cyvcf2.VCF(clinvar_vcf)
    batch_docs, batch_metas, batch_ids = [], [], []
    batch_size = 500
    count = 0

    for variant in vcf:
        clnsig = (variant.INFO.get("CLNSIG", "") or "").lower()
        # Must be P or LP
        if not any(t in clnsig for t in PATHOGENIC_TERMS):
            continue
        # Skip if also has conflicting interpretation
        if "conflicting" in clnsig or "benign" in clnsig:
            continue

        geneinfo = variant.INFO.get("GENEINFO", "") or ""
        gene = geneinfo.split(":")[0] if geneinfo else ""
        if not gene:
            continue

        clndn   = variant.INFO.get("CLNDN",   "") or ""
        clnhgvs = variant.INFO.get("CLNHGVS", "") or ""
        alt     = variant.ALT[0] if variant.ALT else "."
        vid     = f"clngene_{variant.ID or count}"

        # Extract protein position from HGVS if available (e.g. p.Arg123Cys → 123)
        protein_pos = None
        m = re.search(r"p\.[A-Za-z]+(\d+)", clnhgvs)
        if m:
            protein_pos = int(m.group(1))

        doc_text = (
            f"Gene:{gene} "
            f"HGVS:{clnhgvs} "
            f"Variant:{variant.CHROM}:{variant.POS}:{variant.REF}:{alt} "
            f"Significance:{clnsig} "
            f"Disease:{clndn}"
        )

        meta = {
            "gene":   gene,
            "clnsig": clnsig,
            "clndn":  clndn,  # Disease name for PM5 disease matching
            "chrom":  variant.CHROM,
            "pos":    int(variant.POS),
            "ref":    variant.REF,
            "alt":    alt,
        }
        if protein_pos is not None:
            meta["protein_pos"] = protein_pos

        batch_docs.append(doc_text)
        batch_metas.append(meta)
        batch_ids.append(vid)
        count += 1

        if len(batch_docs) >= batch_size:
            collection.add(
                documents=batch_docs,
                metadatas=batch_metas,
                ids=batch_ids,
            )
            batch_docs, batch_metas, batch_ids = [], [], []
            if count % 5000 == 0:
                logger.info(f"  clinvar_gene_variants: {count} indexed...")

    if batch_docs:
        collection.add(documents=batch_docs, metadatas=batch_metas, ids=batch_ids)

    vcf.close()
    logger.info(f"clinvar_gene_variants: DONE — {count} P/LP variants indexed")
    return count


# ---------------------------------------------------------------------------
# Collection 3 — uniprot_domains  (protein domains, sites, regions)
# ---------------------------------------------------------------------------

# UniProt TSV column names as downloaded
_UNIPROT_FEATURE_COLS = [
    "Domain [FT]",
    "Region",
    "Site",
    "Active site",
    "Binding site",
    "Transmembrane",
]

def _parse_uniprot_feature(raw: str) -> list[dict]:
    """
    Parse a UniProt feature column value into a list of feature dicts.

    UniProt TSV format for feature columns:
      DOMAIN 10..120; /note="Kinase"; DOMAIN 130..200; /note="SH2"
    or for sites:
      ACT_SITE 197; /evidence="..."

    Returns list of {"type": str, "start": int, "end": int, "note": str}
    """
    if not raw or raw.strip() == "":
        return []

    results = []
    # Split on feature keyword boundaries
    # Each entry looks like: KEYWORD start[..end]; /note="..."; /evidence="..."
    # We split on capital-letter keywords that start a new feature
    entries = re.split(r'(?=(?:DOMAIN|REGION|SITE|ACT_SITE|BINDING|TRANSMEM)\s+\d)', raw)

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        # Extract positions
        pos_match = re.match(r'(\w+)\s+(\d+)(?:\.\.(\d+))?', entry)
        if not pos_match:
            continue

        feat_type = pos_match.group(1)
        start = int(pos_match.group(2))
        end   = int(pos_match.group(3)) if pos_match.group(3) else start

        # Extract note
        note_match = re.search(r'/note="([^"]+)"', entry)
        note = note_match.group(1) if note_match else ""

        results.append({
            "type":  feat_type,
            "start": start,
            "end":   end,
            "note":  note,
        })

    return results


def build_uniprot_collection():
    """
    Index UniProt protein feature annotations into ChromaDB.
    Each document represents one feature (domain, active site, binding site, etc.)
    for one gene. Used by Agent 5 for PM1.
    """
    import pandas as pd
    from src.config import OPTIONAL_DATABASE_PATHS

    uniprot_path = OPTIONAL_DATABASE_PATHS["uniprot"]
    if not Path(uniprot_path).exists():
        logger.error(f"UniProt file not found at {uniprot_path} — skipping collection")
        return 0

    logger.info(f"Building uniprot_domains from {uniprot_path}")

    client = _get_client()
    ef = _get_ef()

    try:
        client.delete_collection("uniprot_domains")
    except Exception:
        pass

    collection = client.create_collection(
        name="uniprot_domains",
        embedding_function=ef,
        metadata={"description": "UniProt protein domains and functional sites for Agent 5 PM1"},
    )

    df = pd.read_csv(uniprot_path, sep="\t", low_memory=False)
    logger.info(f"  UniProt TSV: {len(df)} proteins loaded")

    batch_docs, batch_metas, batch_ids = [], [], []
    batch_size = 500
    count = 0

    for _, row in df.iterrows():
        # Gene name — UniProt stores space-separated synonyms; take first
        gene_raw = str(row.get("Gene Names", "") or "")
        gene = gene_raw.split()[0] if gene_raw.strip() else ""
        if not gene:
            continue

        protein_name = str(row.get("Protein names", "") or "")

        # Parse all feature columns
        for col in _UNIPROT_FEATURE_COLS:
            raw_val = str(row.get(col, "") or "")
            features = _parse_uniprot_feature(raw_val)

            for feat in features:
                doc_text = (
                    f"Gene:{gene} "
                    f"Protein:{protein_name[:80]} "
                    f"FeatureType:{feat['type']} "
                    f"Positions:{feat['start']}-{feat['end']} "
                    f"Description:{feat['note']}"
                )

                batch_docs.append(doc_text)
                batch_metas.append({
                    "gene":         gene,
                    "feature_type": feat["type"],
                    "start":        feat["start"],
                    "end":          feat["end"],
                    "note":         feat["note"][:200],
                })
                batch_ids.append(f"uniprot_{gene}_{feat['type']}_{feat['start']}_{count}")
                count += 1

                if len(batch_docs) >= batch_size:
                    collection.add(
                        documents=batch_docs,
                        metadatas=batch_metas,
                        ids=batch_ids,
                    )
                    batch_docs, batch_metas, batch_ids = [], [], []

    if batch_docs:
        collection.add(documents=batch_docs, metadatas=batch_metas, ids=batch_ids)

    logger.info(f"uniprot_domains: DONE — {count} feature records indexed")
    return count


# ---------------------------------------------------------------------------
# Helper: Build ClinVar for both builds
# ---------------------------------------------------------------------------

def build_clinvar_all_builds():
    """
    Build ClinVar collections for both GRCh38 and GRCh37.
    Creates 4 collections total:
      - clinvar_variants_grch38
      - clinvar_variants_grch37
      - clinvar_gene_variants_grch38
      - clinvar_gene_variants_grch37
    """
    results = {}

    for build in ["GRCh38", "GRCh37"]:
        logger.info("=" * 70)
        logger.info(f"Building ClinVar collections for {build}")
        logger.info("=" * 70)

        # Build clinvar_variants
        count1 = build_clinvar_collection(genome_build=build)
        results[f"clinvar_variants_{build.lower()}"] = count1

        # Build clinvar_gene_variants
        count2 = build_clinvar_gene_collection(genome_build=build)
        results[f"clinvar_gene_variants_{build.lower()}"] = count2

        logger.info(f"✅ {build} ClinVar collections complete")
        logger.info("")

    return results


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def build_all(skip_existing: bool = False, genome_builds: list = None):
    """
    Build all RAG collections.

    Args:
        skip_existing: If True, skip collections that already exist in ChromaDB.
                       Set to False (default) to always rebuild fresh.
        genome_builds: List of builds to create ClinVar collections for.
                      Default: ["GRCh38", "GRCh37"] (both builds)
    """
    if genome_builds is None:
        genome_builds = ["GRCh38", "GRCh37"]

    client = _get_client()
    existing = {c.name for c in client.list_collections()}

    results = {}

    # Build ClinVar for all specified builds
    for build in genome_builds:
        build_lower = build.lower()
        coll_variants = f"clinvar_variants_{build_lower}"
        coll_gene = f"clinvar_gene_variants_{build_lower}"

        if skip_existing and coll_variants in existing:
            logger.info(f"{coll_variants} already exists — skipping")
        else:
            results[coll_variants] = build_clinvar_collection(genome_build=build)

        if skip_existing and coll_gene in existing:
            logger.info(f"{coll_gene} already exists — skipping")
        else:
            results[coll_gene] = build_clinvar_gene_collection(genome_build=build)

    # Build UniProt (build-agnostic)
    if skip_existing and "uniprot_domains" in existing:
        logger.info("uniprot_domains already exists — skipping")
    else:
        results["uniprot_domains"] = build_uniprot_collection()

    logger.info("=" * 60)
    logger.info("RAG BUILD COMPLETE")
    for name, n in results.items():
        logger.info(f"  {name}: {n} records")
    logger.info("=" * 60)
    return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("/home/ubuntu/ai-native-acmg-pipeline/logs/rag_build.log"),
        ],
    )

    import argparse
    parser = argparse.ArgumentParser(description="Build ACMG pipeline RAG collections")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip collections that already exist (faster re-runs if partially built)",
    )
    parser.add_argument(
        "--collection",
        choices=["clinvar", "clinvar_gene", "uniprot", "all"],
        default="all",
        help="Which collection to build (default: all)",
    )
    args = parser.parse_args()

    if args.collection == "all":
        build_all(skip_existing=args.skip_existing)
    elif args.collection == "clinvar":
        build_clinvar_collection()
    elif args.collection == "clinvar_gene":
        build_clinvar_gene_collection()
    elif args.collection == "uniprot":
        build_uniprot_collection()


