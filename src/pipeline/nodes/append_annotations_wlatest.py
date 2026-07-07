"""
src/pipeline/nodes/append_annotations.py

Node that appends parquet-based annotations to VEP TSV output.

Reads partial VEP TSV (without ClinVar/dbNSFP/SpliceAI plugins),
looks up annotations from parquet files, and writes complete TSV.
"""

import logging
import pandas as pd
from pathlib import Path
from typing import Set

from src.pipeline.state import VariantState
from src.pipeline.nodes.parquet_annotator import ParquetAnnotator

logger = logging.getLogger(__name__)


def extract_chromosomes_from_tsv(tsv_path: Path) -> Set[str]:
    """
    Fast scan of VEP TSV to extract unique chromosomes.

    Reads only the Location column.
    """

    logger.info(f"Extracting chromosomes from {tsv_path.name}")

    chromosomes = set()

    with open(tsv_path, "r") as f:

        header = None

        # Skip metadata lines (##), stop at actual header (#Uploaded_variation...)
        for line in f:

            if line.startswith("##"):
                continue

            if line.startswith("#"):
                header = line.lstrip("#").rstrip("\n").split("\t")
                break

            break

        if header is None:
            logger.error("VEP TSV header not found")
            return set()

        try:
            location_idx = header.index("Location")
        except ValueError:
            logger.error(f"Location column not found. Header = {header}")
            return set()

        for line in f:

            cols = line.rstrip("\n").split("\t")

            if len(cols) <= location_idx:
                continue

            location = cols[location_idx]

            chrom = location.split(":")[0].removeprefix("chr")

            chromosomes.add(chrom)

    logger.info(f"Found {len(chromosomes)} chromosomes: {sorted(chromosomes)}")

    return chromosomes


def append_annotations_node(state: VariantState) -> dict:
    """
    Append parquet-based annotations to partial VEP TSV.

    Reads:
        - state["annotated_tsv"]: Partial VEP TSV (without ClinVar/dbNSFP/SpliceAI)
        - state["genome_build"]: GRCh37 or GRCh38

    Writes:
        - Complete VEP TSV with all annotations

    Returns:
        - {"annotated_tsv": path_to_complete_tsv}
    """
    session_id = state["session_id"]
    partial_tsv_path = Path(state["annotated_tsv"])
    genome_build = state.get("genome_build", "GRCh38")
    filtered_vcf = Path(state["filtered_vcf"])

    logger.info(f"[{session_id}] Appending parquet annotations to {partial_tsv_path.name}")

    # Step 1: Extract chromosomes from TSV (fast scan)
    chromosomes_needed = extract_chromosomes_from_tsv(partial_tsv_path)

    if not chromosomes_needed:
        logger.warning(f"[{session_id}] No chromosomes found in TSV - skipping annotation")
        return {}

    # Step 2: Initialize annotator (loads parquet data for needed chromosomes only)
    try:
        annotator = ParquetAnnotator(genome_build, chromosomes_needed)
    except Exception as e:
        logger.error(f"[{session_id}] Failed to initialize ParquetAnnotator: {e}", exc_info=True)
        # Degrade gracefully - return partial TSV unchanged
        return {}

    # Step 3: Read VEP TSV
    logger.info(f"[{session_id}] Reading VEP TSV...")

    # Read header comments (lines starting with ##)
    header_lines = []
    with open(partial_tsv_path, "r") as f:
        for line in f:
            if line.startswith("##"):
                header_lines.append(line)
            else:
                break

    # Read data table
    df = pd.read_csv(partial_tsv_path, sep="\t", comment="#", low_memory=False)
    original_row_count = len(df)
    logger.info(f"[{session_id}] Loaded {original_row_count:,} rows")

    # Step 4: Add annotation columns (initialize empty)
    annotation_columns = [
        # ClinVar
        "ClinVar_CLNSIG", "ClinVar_CLNREVSTAT", "ClinVar_CLNDN",
        # dbNSFP
        "REVEL_score", "CADD_phred", "Polyphen2_HDIV_score", "SIFT_score",
        "phyloP100way_vertebrate", "GERP++_RS", "MutationTaster_pred", "MetaSVM_score",
        "Ensembl_transcriptid",  # Already in VEP output, but keep for consistency
        # SpliceAI
        "SpliceAI_pred",
    ]

    for col in annotation_columns:
        if col not in df.columns:
            df[col] = ""

    # Step 5: Annotate each row
    logger.info(f"[{session_id}] Annotating {original_row_count:,} rows...")

    logger.info(f"[{session_id}] Building REF lookup from {filtered_vcf.name}")

    import subprocess

    ref_lookup = {}

    result = subprocess.run(
        [
            "bcftools",
            "query",
            "-f",
            "%CHROM\t%POS\t%REF\t%ALT\n",
            str(filtered_vcf),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    for line in result.stdout.splitlines():

        chrom, pos, ref, alts = line.split("\t")

        chrom = chrom.removeprefix("chr")

        for alt in alts.split(","):
            ref_lookup[(chrom, int(pos), alt)] = ref

    logger.info(
        f"[{session_id}] Built REF lookup for {len(ref_lookup):,} alleles"
    )

    annotated_count = 0

    missing_ref = 0

    for idx, row in df.iterrows():
        # Extract variant coordinates from Location column
        # Location format: "1:12345" or "1:12345-12346"
        location = str(row.get("Location", ""))
        if not location or ":" not in location:
            continue

        chrom = location.split(":")[0]
        pos_str = location.split(":")[1].split("-")[0]
        try:
            pos = int(pos_str)
        except ValueError:
            continue

        chrom = location.split(":")[0].removeprefix("chr")

        pos_str = location.split(":")[1].split("-")[0]

        try:
            pos = int(pos_str)
        except ValueError:
            continue
        
        alt = str(row.get("Allele", ""))

        ref = ref_lookup.get((chrom, pos, alt), "")

        # Transcript ID from Feature column
        transcript_id = str(row.get("Feature", ""))

        # Variant class from VARIANT_CLASS column
        variant_class = str(row.get("VARIANT_CLASS", "SNV"))

        # REF lookup failed
        if not ref:
        
            missing_ref += 1

            # Only log the first few so the logs don't become huge
            if missing_ref <= 10:
                logger.warning(
                    f"[{session_id}] REF lookup failed for {chrom}:{pos} ALT={alt}"
                )

            continue
        
        # Skip if other critical fields are missing
        if not all([chrom, pos, alt]):
            continue

        # Lookup annotations
        try:
            annotations = annotator.annotate_variant(
                chrom=chrom,
                pos=pos,
                ref=ref,
                alt=alt,
                transcript_id=transcript_id,
                variant_class=variant_class
            )

            # Inject into dataframe
            for col, value in annotations.items():
                if col in df.columns:
                    df.at[idx, col] = value

            if annotations:
                annotated_count += 1

        except Exception as e:
            logger.warning(f"[{session_id}] Failed to annotate row {idx}: {e}")
            continue

    logger.info(f"[{session_id}] Annotated {annotated_count}/{original_row_count} rows")

    logger.info(
    f"[{session_id}] Missing REF for {missing_ref:,} variants"
    )
    
    # Step 6: Write complete TSV
    complete_tsv_path = partial_tsv_path.parent / f"{session_id}_vep_complete.tsv"

    logger.info(f"[{session_id}] Writing complete TSV to {complete_tsv_path.name}")

    with open(complete_tsv_path, "w") as f:
        # Write header comments
        f.writelines(header_lines)

        # Write data table
        df.to_csv(f, sep="\t", index=False)

    logger.info(f"[{session_id}] ✓ Complete TSV written: {complete_tsv_path}")

    return {
        "annotated_tsv": str(complete_tsv_path),
    }

