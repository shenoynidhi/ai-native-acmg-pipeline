"""
Complete Parquet Database Builder - All Reference Databases

Converts ALL reference databases to Parquet format for 100-500× faster queries.

NEW in this version:
  - ClinGen gene-disease validity
  - HGNC gene symbols
  - Orphanet inheritance patterns
  - RepeatMasker repeat regions
  - OMIM disease information

Usage:
    python -m src.rag.parquet_builder_complete
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
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

from src.config import get_database_paths, DATABASE_DIR, DATABASE_PATHS, OPTIONAL_DATABASE_PATHS

PARQUET_DIR = DATABASE_DIR / "parquet"
PARQUET_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Previous collections (ClinVar, gnomAD, UniProt, HPO) - keep as before
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


def build_clinvar_parquet(genome_build: str = "GRCh38"):
    """Build ClinVar Parquet (already implemented)."""
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
        clnrevstat = variant.INFO.get("CLNREVSTAT", "") or ""
        stars = _revstat_to_stars(clnrevstat)
        if stars < 2:
            skipped += 1
            continue

        clnsig = variant.INFO.get("CLNSIG", "") or ""
        clndn = variant.INFO.get("CLNDN", "") or ""
        geneinfo = variant.INFO.get("GENEINFO", "") or ""
        gene = geneinfo.split(":")[0] if geneinfo else ""
        alt = variant.ALT[0] if variant.ALT else "."

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

    df = pd.DataFrame(records)
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
    return count


def build_gnomad_constraint_parquet():
    """Build gnomAD constraint Parquet (already implemented)."""
    gnomad_path = DATABASE_PATHS["gnomad_constraint"]

    if not Path(gnomad_path).exists():
        logger.error(f"gnomAD constraint file not found: {gnomad_path}")
        return 0

    logger.info(f"Building gnomAD constraint Parquet from {gnomad_path}")

    df = pd.read_csv(gnomad_path, sep="\t", low_memory=False, compression='infer')

    columns_to_keep = [
        "gene", "transcript", "pLI", "pNull", "pRec",
        "oe_lof", "oe_lof_lower", "oe_lof_upper",
        "oe_mis", "oe_mis_lower", "oe_mis_upper",
        "mis_z", "syn_z", "lof_z",
        "exp_lof", "exp_mis", "exp_syn",
        "obs_lof", "obs_mis", "obs_syn",
    ]

    columns_to_keep = [c for c in columns_to_keep if c in df.columns]
    df = df[columns_to_keep]

    output_path = PARQUET_DIR / "gnomad_constraint.parquet"
    df.to_parquet(output_path, compression="snappy", index=False)

    logger.info(f"✅ gnomAD constraint: {len(df)} genes → {output_path}")
    return len(df)


def build_uniprot_parquet():
    """Build UniProt Parquet (already implemented)."""
    uniprot_path = OPTIONAL_DATABASE_PATHS["uniprot"]

    if not Path(uniprot_path).exists():
        logger.error(f"UniProt file not found: {uniprot_path}")
        return 0

    logger.info(f"Building UniProt Parquet from {uniprot_path}")

    df = pd.read_csv(uniprot_path, sep="\t", low_memory=False)
    df["gene"] = df["Gene Names"].str.split().str[0]

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

        for col in feature_cols:
            if col not in df.columns:
                continue

            raw_val = row[col]
            if pd.isna(raw_val) or not str(raw_val).strip():
                continue

            raw_val = str(raw_val)
            entries = re.split(r'(?=(?:DOMAIN|REGION|SITE|ACT_SITE|BINDING|TRANSMEM)\s+\d)', raw_val)

            for entry in entries:
                entry = entry.strip()
                if not entry:
                    continue

                pos_match = re.match(r'(\w+)\s+(\d+)(?:\.\.(\d+))?', entry)
                if not pos_match:
                    continue

                feat_type = pos_match.group(1)
                start = int(pos_match.group(2))
                end = int(pos_match.group(3)) if pos_match.group(3) else start

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

    df_features = pd.DataFrame(records)
    output_path = PARQUET_DIR / "uniprot_domains.parquet"
    df_features.to_parquet(output_path, compression="snappy", index=False)

    logger.info(f"✅ UniProt domains: {len(df_features)} features → {output_path}")
    return len(df_features)


def build_hpo_parquet():
    """Build HPO Parquet (already implemented)."""
    hpo_path = DATABASE_PATHS["hpo_annotations"]

    if not Path(hpo_path).exists():
        logger.error(f"HPO annotations file not found: {hpo_path}")
        return 0

    logger.info(f"Building HPO Parquet from {hpo_path}")

    df = pd.read_csv(hpo_path, sep="\t", comment="#", low_memory=False)
    df.columns = [
        "database_id", "disease_name", "qualifier", "hpo_id", "reference",
        "evidence", "onset", "frequency", "sex", "modifier", "aspect",
        "biocuration"
    ]

    output_path = PARQUET_DIR / "hpo_annotations.parquet"
    df.to_parquet(output_path, compression="snappy", index=False)

    logger.info(f"✅ HPO annotations: {len(df)} entries → {output_path}")
    return len(df)


# ---------------------------------------------------------------------------
# NEW Collection 6: ClinGen Gene-Disease Validity
# ---------------------------------------------------------------------------

def build_clingen_parquet():
    """
    Convert ClinGen gene-disease validity CSV → Parquet.

    Input:  data/databases/clingen/gene_disease_validity.csv
    Output: data/parquet/clingen_validity.parquet

    Used by: Agent 2 (PVS1 - check if gene LoF mechanism matches disease)
    """
    clingen_path = DATABASE_PATHS["clingen_validity"]

    if not Path(clingen_path).exists():
        logger.error(f"ClinGen validity file not found: {clingen_path}")
        return 0

    logger.info(f"Building ClinGen validity Parquet from {clingen_path}")

    # Read CSV
    df = pd.read_csv(clingen_path, low_memory=False)

    # Normalize column names (ClinGen CSV may have different formats)
    # Common columns: Gene Symbol, Disease, MOI (Mode of Inheritance), Classification

    # Select relevant columns
    columns_to_keep = []
    for col in df.columns:
        col_lower = col.lower()
        if any(x in col_lower for x in ['gene', 'symbol', 'hgnc', 'disease', 'moi', 'inheritance', 'classification', 'validity', 'mechanism']):
            columns_to_keep.append(col)

    if columns_to_keep:
        df = df[columns_to_keep]

    # Write to Parquet
    output_path = PARQUET_DIR / "clingen_validity.parquet"
    df.to_parquet(output_path, compression="snappy", index=False)

    logger.info(f"✅ ClinGen validity: {len(df)} gene-disease pairs → {output_path}")
    return len(df)


# ---------------------------------------------------------------------------
# NEW Collection 7: HGNC Gene Symbols
# ---------------------------------------------------------------------------

def build_hgnc_parquet():
    """
    Convert HGNC gene symbols TSV → Parquet.

    Input:  data/databases/hgnc/hgnc_complete_set.txt
    Output: data/parquet/hgnc_genes.parquet

    Used by: Gene symbol normalization across all agents
    """
    hgnc_path = DATABASE_PATHS["hgnc"]

    if not Path(hgnc_path).exists():
        logger.error(f"HGNC file not found: {hgnc_path}")
        return 0

    logger.info(f"Building HGNC Parquet from {hgnc_path}")

    # Read TSV
    df = pd.read_csv(hgnc_path, sep="\t", low_memory=False)

    # Select relevant columns
    columns_to_keep = []
    for col in df.columns:
        col_lower = col.lower()
        if any(x in col_lower for x in ['symbol', 'name', 'alias', 'previous', 'hgnc_id', 'entrez', 'ensembl', 'status']):
            columns_to_keep.append(col)

    if columns_to_keep:
        df = df[columns_to_keep]

    # Write to Parquet
    output_path = PARQUET_DIR / "hgnc_genes.parquet"
    df.to_parquet(output_path, compression="snappy", index=False)

    logger.info(f"✅ HGNC genes: {len(df)} genes → {output_path}")
    return len(df)


# ---------------------------------------------------------------------------
# NEW Collection 8: Orphanet Inheritance Patterns
# ---------------------------------------------------------------------------

def build_orphanet_parquet():
    """
    Convert Orphanet XML → Parquet.

    Input:  data/databases/orphanet/genes_diseases.xml
    Output: data/parquet/orphanet_inheritance.parquet

    Used by: Agent 6, 8 (inheritance pattern checks)
    """
    orphanet_xml = OPTIONAL_DATABASE_PATHS["orphanet_genes"]

    if not Path(orphanet_xml).exists():
        logger.error(f"Orphanet XML not found: {orphanet_xml}")
        return 0

    logger.info(f"Building Orphanet Parquet from {orphanet_xml}")

    # Parse XML
    tree = ET.parse(orphanet_xml)
    root = tree.getroot()

    records = []

    # Orphanet XML structure: <DisorderList> -> <Disorder> -> <DisorderGeneAssociationList>
    for disorder in root.findall(".//Disorder"):
        disorder_name = disorder.find(".//Name").text if disorder.find(".//Name") is not None else ""
        orpha_code = disorder.find(".//OrphaCode").text if disorder.find(".//OrphaCode") is not None else ""

        for gene_assoc in disorder.findall(".//DisorderGeneAssociation"):
            gene_elem = gene_assoc.find(".//Gene")
            if gene_elem is None:
                continue

            gene_symbol = gene_elem.find(".//Symbol").text if gene_elem.find(".//Symbol") is not None else ""
            gene_id = gene_elem.find(".//GeneID").text if gene_elem.find(".//GeneID") is not None else ""

            # Inheritance pattern
            inheritance_elem = disorder.find(".//TypeOfInheritanceList/TypeOfInheritance/Name")
            inheritance = inheritance_elem.text if inheritance_elem is not None else ""

            records.append({
                "gene": gene_symbol,
                "gene_id": gene_id,
                "disease_name": disorder_name,
                "orpha_code": orpha_code,
                "inheritance": inheritance,
            })

    if not records:
        logger.warning("No Orphanet records parsed!")
        return 0

    df = pd.DataFrame(records)

    # Write to Parquet
    output_path = PARQUET_DIR / "orphanet_inheritance.parquet"
    df.to_parquet(output_path, compression="snappy", index=False)

    logger.info(f"✅ Orphanet inheritance: {len(df)} gene-disease pairs → {output_path}")
    return len(df)


# ---------------------------------------------------------------------------
# NEW Collection 9: RepeatMasker
# ---------------------------------------------------------------------------

def build_repeatmasker_parquet():
    """
    Convert RepeatMasker BED.gz → Parquet.

    Input:  data/databases/repeatmasker/repeatmasker.bed.gz
    Output: data/parquet/repeatmasker.parquet

    Used by: Agent 8 (PM4/BP3 - check if variant in repeat region)
    """
    repeatmasker_path = OPTIONAL_DATABASE_PATHS["repeatmasker"]

    if not Path(repeatmasker_path).exists():
        logger.error(f"RepeatMasker file not found: {repeatmasker_path}")
        return 0

    logger.info(f"Building RepeatMasker Parquet from {repeatmasker_path}")

    # Read BED.gz (3-column BED: chrom, start, end)
    df = pd.read_csv(
        repeatmasker_path,
        sep="\t",
        compression="gzip",
        header=None,
        names=["chrom", "start", "end", "repeat_name", "score", "strand", "repeat_class", "repeat_family"],
        low_memory=False
    )

    # Select relevant columns
    df = df[["chrom", "start", "end", "repeat_name", "repeat_class", "repeat_family"]]

    # Write to Parquet (partitioned by chromosome for fast queries)
    output_dir = PARQUET_DIR / "repeatmasker"

    table = pa.Table.from_pandas(df)
    pq.write_to_dataset(
        table,
        root_path=str(output_dir),
        partition_cols=["chrom"],
        compression="snappy",
        existing_data_behavior="overwrite_or_ignore",
    )

    logger.info(f"✅ RepeatMasker: {len(df)} regions → {output_dir}")
    return len(df)


# ---------------------------------------------------------------------------
# NEW Collection 10: OMIM (if needed)
# ---------------------------------------------------------------------------

def build_omim_parquet():
    """
    Convert OMIM morbidmap.txt → Parquet.

    Input:  data/databases/omim_tmp/morbidmap.txt
    Output: data/parquet/omim_morbidmap.parquet

    Used by: Disease information lookups (optional)
    """
    omim_path = OPTIONAL_DATABASE_PATHS["omim_morbidmap"]

    if not Path(omim_path).exists():
        logger.warning(f"OMIM file not found: {omim_path} (optional)")
        return 0

    logger.info(f"Building OMIM Parquet from {omim_path}")

    # OMIM morbidmap format: Phenotype | Gene Symbols | MIM Number | Cyto Location
    records = []

    with open(omim_path, 'r') as f:
        for line in f:
            if line.startswith('#'):
                continue

            parts = line.strip().split('|')
            if len(parts) < 4:
                continue

            phenotype = parts[0].strip()
            genes = parts[1].strip()
            mim_number = parts[2].strip()
            cyto_location = parts[3].strip()

            records.append({
                "phenotype": phenotype,
                "genes": genes,
                "mim_number": mim_number,
                "cyto_location": cyto_location,
            })

    if not records:
        logger.warning("No OMIM records parsed!")
        return 0

    df = pd.DataFrame(records)

    # Write to Parquet
    output_path = PARQUET_DIR / "omim_morbidmap.parquet"
    df.to_parquet(output_path, compression="snappy", index=False)

    logger.info(f"✅ OMIM morbidmap: {len(df)} phenotypes → {output_path}")
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

    # NEW: Build ClinGen
    try:
        count = build_clingen_parquet()
        results["clingen_validity"] = count
    except Exception as e:
        logger.error(f"Failed to build ClinGen: {e}")
        results["clingen_validity"] = 0

    # NEW: Build HGNC
    try:
        count = build_hgnc_parquet()
        results["hgnc_genes"] = count
    except Exception as e:
        logger.error(f"Failed to build HGNC: {e}")
        results["hgnc_genes"] = 0

    # NEW: Build Orphanet
    try:
        count = build_orphanet_parquet()
        results["orphanet_inheritance"] = count
    except Exception as e:
        logger.error(f"Failed to build Orphanet: {e}")
        results["orphanet_inheritance"] = 0

    # NEW: Build RepeatMasker
    try:
        count = build_repeatmasker_parquet()
        results["repeatmasker"] = count
    except Exception as e:
        logger.error(f"Failed to build RepeatMasker: {e}")
        results["repeatmasker"] = 0

    # NEW: Build OMIM (optional)
    try:
        count = build_omim_parquet()
        results["omim_morbidmap"] = count
    except Exception as e:
        logger.warning(f"Failed to build OMIM (optional): {e}")
        results["omim_morbidmap"] = 0

    # Summary
    logger.info("=" * 60)
    logger.info("COMPLETE PARQUET BUILD")
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

    parser = argparse.ArgumentParser(description="Build ALL Parquet databases for ACMG pipeline")
    parser.add_argument(
        "--collection",
        choices=["clinvar", "gnomad", "uniprot", "hpo", "clingen", "hgnc", "orphanet", "repeatmasker", "omim", "all"],
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
    elif args.collection == "clingen":
        build_clingen_parquet()
    elif args.collection == "hgnc":
        build_hgnc_parquet()
    elif args.collection == "orphanet":
        build_orphanet_parquet()
    elif args.collection == "repeatmasker":
        build_repeatmasker_parquet()
    elif args.collection == "omim":
        build_omim_parquet()

