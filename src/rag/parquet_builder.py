"""
Parquet Database Builder for ACMG Pipeline

Converts reference databases from gzipped/TSV formats to Parquet for fast lookups.
Run once, then query with DuckDB (zero-copy, no loading into memory).

Collections built:
  1. clinvar_grch38.parquet       — ClinVar variants (PS1, PP5, BP6)
  2. clinvar_grch37.parquet       — ClinVar variants (GRCh37)
  3. gnomad_constraint.parquet    — gnomAD gene constraints (pLI, LOEUF)
  4. uniprot_domains.parquet      — UniProt functional domains (PM1)
  5. hpo_annotations.parquet      — HPO gene-disease associations (PP4)

Usage:
    python -m src.rag.parquet_builder --all
    python -m src.rag.parquet_builder --collection clinvar  # Single collection
"""

import logging
import sys
import argparse
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import cyvcf2
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from src.config import get_database_paths, DATABASE_DIR, DATABASE_PATHS

PARQUET_DIR = DATABASE_DIR / "parquet"
PARQUET_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helper: ClinVar star rating
# ---------------------------------------------------------------------------

def _revstat_to_stars(revstat: str) -> int:
    """Convert ClinVar CLNREVSTAT to star rating (0-4)."""
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
# Collection 1: ClinVar Variants (both builds)
# ---------------------------------------------------------------------------

def build_clinvar_parquet(genome_build: str = "GRCh38"):
    """
    Convert ClinVar VCF → Parquet (partitioned by chromosome).
    
    Args:
        genome_build: "GRCh38" or "GRCh37"
    
    Output:
        data/parquet/clinvar_{build}/chrom=1/*.parquet
        data/parquet/clinvar_{build}/chrom=2/*.parquet
        ...
    """
    db_paths = get_database_paths(genome_build)
    clinvar_vcf = str(db_paths["clinvar_vcf"])
    
    if not Path(clinvar_vcf).exists():
        logger.error(f"ClinVar VCF not found: {clinvar_vcf}")
        return 0
    
    logger.info(f"Building ClinVar Parquet for {genome_build} from {clinvar_vcf}")
    
    vcf = cyvcf2.VCF(clinvar_vcf)
    
    records = []
    count = 0
    skipped = 0
    
    for variant in vcf:
        # Filter: ≥2 stars only
        clnrevstat = variant.INFO.get("CLNREVSTAT", "") or ""
        stars = _revstat_to_stars(clnrevstat)
        if stars < 2:
            skipped += 1
            continue
        
        # Extract fields
        clnsig = variant.INFO.get("CLNSIG", "") or ""
        clndn = variant.INFO.get("CLNDN", "") or ""
        geneinfo = variant.INFO.get("GENEINFO", "") or ""
        gene = geneinfo.split(":")[0] if geneinfo else ""
        alt = variant.ALT[0] if variant.ALT else "."
        
        # Extract protein position from CLNHGVS if available
        clnhgvs = variant.INFO.get("CLNHGVS", "") or ""
        protein_pos = None
        m = re.search(r"p\.[A-Za-z]+(\d+)", clnhgvs)
        if m:
            protein_pos = int(m.group(1))
        
        records.append({
            "chrom": variant.CHROM,
            "pos": int(variant.POS),
            "ref": variant.REF,
            "alt": alt,
            "gene": gene,
            "clnsig": clnsig,
            "clndn": clndn,
            "stars": stars,
            "hgvs": clnhgvs,
            "protein_pos": protein_pos,
            "variant_id": variant.ID or f"cv_{count}",
        })
        
        count += 1
        
        if count % 10000 == 0:
            logger.info(f"  Processed {count} variants ({skipped} skipped)...")
    
    vcf.close()
    
    if not records:
        logger.warning("No ClinVar records ≥2 stars found!")
        return 0
    
    # Convert to DataFrame
    df = pd.DataFrame(records)
    
    # Write to partitioned Parquet
    build_lower = genome_build.lower().replace("grch", "grch")
    output_dir = PARQUET_DIR / f"clinvar_{build_lower}"
    
    table = pa.Table.from_pandas(df)
    pq.write_to_dataset(
        table,
        root_path=str(output_dir),
        partition_cols=["chrom"],
        compression="snappy",
        existing_data_behavior="overwrite_or_ignore",
    )
    
    logger.info(f"✅ ClinVar {genome_build}: {count} variants → {output_dir}")
    logger.info(f"   Skipped: {skipped} variants (<2 stars)")
    
    return count


# ---------------------------------------------------------------------------
# Collection 2: gnomAD Constraint Scores
# ---------------------------------------------------------------------------

def build_gnomad_constraint_parquet():
    """
    Convert gnomAD constraint TSV → Parquet.
    
    Input:  data/databases/gnomad/gnomad.v2.1.1.lof_metrics.by_gene.txt
    Output: data/parquet/gnomad_constraint.parquet
    """
    gnomad_path = DATABASE_PATHS["gnomad_constraint"]
    
    if not Path(gnomad_path).exists():
        logger.error(f"gnomAD constraint file not found: {gnomad_path}")
        return 0
    
    logger.info(f"Building gnomAD constraint Parquet from {gnomad_path}")
    
    # Read TSV with pandas
    df = pd.read_csv(gnomad_path, sep="\t", low_memory=False)
    
    # Select relevant columns only
    columns_to_keep = [
        "gene", "transcript", "pLI", "pNull", "pRec",
        "oe_lof", "oe_lof_lower", "oe_lof_upper",
        "oe_mis", "oe_mis_lower", "oe_mis_upper",
        "mis_z", "syn_z", "lof_z",
        "exp_lof", "exp_mis", "exp_syn",
        "obs_lof", "obs_mis", "obs_syn",
    ]
    
    # Filter to columns that exist
    columns_to_keep = [c for c in columns_to_keep if c in df.columns]
    df = df[columns_to_keep]
    
    # Write to Parquet
    output_path = PARQUET_DIR / "gnomad_constraint.parquet"
    df.to_parquet(output_path, compression="snappy", index=False)
    
    logger.info(f"✅ gnomAD constraint: {len(df)} genes → {output_path}")
    
    return len(df)


# ---------------------------------------------------------------------------
# Collection 3: UniProt Domains
# ---------------------------------------------------------------------------

def build_uniprot_parquet():
    """
    Convert UniProt TSV → Parquet.
    
    Input:  data/databases/uniprot/uniprot_human_features.tsv
    Output: data/parquet/uniprot_domains.parquet
    """
    from src.config import OPTIONAL_DATABASE_PATHS
    
    uniprot_path = OPTIONAL_DATABASE_PATHS["uniprot"]
    
    if not Path(uniprot_path).exists():
        logger.error(f"UniProt file not found: {uniprot_path}")
        return 0
    
    logger.info(f"Building UniProt Parquet from {uniprot_path}")
    
    # Read TSV
    df = pd.read_csv(uniprot_path, sep="\t", low_memory=False)
    
    # Extract gene name (first word of "Gene Names" column)
    df["gene"] = df["Gene Names"].str.split().str[0]
    
    # Parse domain/site columns
    feature_cols = [
        "Domain [FT]", "Region", "Site", "Active site",
        "Binding site", "Transmembrane"
    ]
    
    records = []
    
    for _, row in df.iterrows():
        gene = row["gene"]
        if not gene or pd.isna(gene):
            continue
        
        protein_name = row.get("Protein names", "")
        
        # Parse each feature column
        for col in feature_cols:
            if col not in df.columns:
                continue
            
            raw_val = row[col]
            if pd.isna(raw_val) or not str(raw_val).strip():
                continue
            
            # Parse features (e.g., "DOMAIN 10..120; /note='Kinase'")
            raw_val = str(raw_val)
            entries = re.split(r'(?=(?:DOMAIN|REGION|SITE|ACT_SITE|BINDING|TRANSMEM)\s+\d)', raw_val)
            
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
                end = int(pos_match.group(3)) if pos_match.group(3) else start
                
                # Extract note
                note_match = re.search(r'/note="([^"]+)"', entry)
                note = note_match.group(1) if note_match else ""
                
                records.append({
                    "gene": gene,
                    "protein_name": protein_name[:100],
                    "feature_type": feat_type,
                    "start": start,
                    "end": end,
                    "note": note[:200],
                })
    
    if not records:
        logger.warning("No UniProt features parsed!")
        return 0
    
    # Convert to DataFrame
    df_features = pd.DataFrame(records)
    
    # Write to Parquet
    output_path = PARQUET_DIR / "uniprot_domains.parquet"
    df_features.to_parquet(output_path, compression="snappy", index=False)
    
    logger.info(f"✅ UniProt domains: {len(df_features)} features → {output_path}")
    
    return len(df_features)


# ---------------------------------------------------------------------------
# Collection 4: HPO Annotations
# ---------------------------------------------------------------------------

def build_hpo_parquet():
    """
    Convert HPO annotations → Parquet.
    
    Input:  data/databases/hpo/phenotype.hpoa
    Output: data/parquet/hpo_annotations.parquet
    """
    hpo_path = DATABASE_PATHS["hpo_annotations"]
    
    if not Path(hpo_path).exists():
        logger.error(f"HPO annotations file not found: {hpo_path}")
        return 0
    
    logger.info(f"Building HPO Parquet from {hpo_path}")
    
    # Read TSV (skip comment lines)
    df = pd.read_csv(hpo_path, sep="\t", comment="#", low_memory=False)
    
    # Rename columns for clarity
    df.columns = [
        "database_id", "disease_name", "qualifier", "hpo_id", "reference",
        "evidence", "onset", "frequency", "sex", "modifier", "aspect",
        "biocuration"
    ]
    
    # Extract gene symbol from database_id (if present)
    # Format: OMIM:123456, ORPHA:123, etc.
    # For now, keep as-is (will join with Orphanet for gene mapping)
    
    # Write to Parquet
    output_path = PARQUET_DIR / "hpo_annotations.parquet"
    df.to_parquet(output_path, compression="snappy", index=False)
    
    logger.info(f"✅ HPO annotations: {len(df)} entries → {output_path}")
    
    return len(df)


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_all(skip_existing: bool = False):
    """Build all Parquet collections."""
    results = {}
    
    # Build ClinVar for both builds
    for build in ["GRCh38", "GRCh37"]:
        try:
            count = build_clinvar_parquet(genome_build=build)
            results[f"clinvar_{build.lower()}"] = count
        except Exception as e:
            logger.error(f"Failed to build ClinVar {build}: {e}")
            results[f"clinvar_{build.lower()}"] = 0
    
    # Build gnomAD constraint
    try:
        count = build_gnomad_constraint_parquet()
        results["gnomad_constraint"] = count
    except Exception as e:
        logger.error(f"Failed to build gnomAD constraint: {e}")
        results["gnomad_constraint"] = 0
    
    # Build UniProt
    try:
        count = build_uniprot_parquet()
        results["uniprot_domains"] = count
    except Exception as e:
        logger.error(f"Failed to build UniProt: {e}")
        results["uniprot_domains"] = 0
    
    # Build HPO
    try:
        count = build_hpo_parquet()
        results["hpo_annotations"] = count
    except Exception as e:
        logger.error(f"Failed to build HPO: {e}")
        results["hpo_annotations"] = 0
    
    # Summary
    logger.info("=" * 60)
    logger.info("PARQUET BUILD COMPLETE")
    for name, count in results.items():
        logger.info(f"  {name}: {count} records")
    logger.info(f"Output directory: {PARQUET_DIR}")
    logger.info("=" * 60)
    
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s — %(message)s",
    )
    
    parser = argparse.ArgumentParser(description="Build Parquet databases for ACMG pipeline")
    parser.add_argument(
        "--collection",
        choices=["clinvar", "gnomad", "uniprot", "hpo", "all"],
        default="all",
        help="Which collection to build (default: all)",
    )
    parser.add_argument(
        "--build",
        choices=["GRCh38", "GRCh37"],
        help="Genome build for ClinVar (if --collection=clinvar)",
    )
    args = parser.parse_args()
    
    if args.collection == "all":
        build_all()
    elif args.collection == "clinvar":
        build = args.build or "GRCh38"
        build_clinvar_parquet(genome_build=build)
    elif args.collection == "gnomad":
        build_gnomad_constraint_parquet()
    elif args.collection == "uniprot":
        build_uniprot_parquet()
    elif args.collection == "hpo":
        build_hpo_parquet()
