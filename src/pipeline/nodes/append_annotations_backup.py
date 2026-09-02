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


def _read_vep_tsv(tsv_path: Path):
    """
    Read a VEP TSV into (header_comment_lines, dataframe).

    VEP TSVs look like:
        ## metadata line 1
        ## metadata line 2
        #Uploaded_variation\tLocation\tAllele\t...
        var1\t1:12345\tA\t...

    IMPORTANT: We do NOT use pd.read_csv(..., comment="#") here.
    pandas' `comment` param strips *every* line starting with the
    comment char - including the single-'#' column header line.
    When that happens pandas silently promotes the first DATA row
    to be the header, every column name becomes garbage (the values
    of row 0), and row.get("Location", "") / row.get("Allele", "")
    return "" for every subsequent row. That's a totally silent
    failure - no exception, no empty dataframe, just wrong column
    names - which is exactly what was happening here: the loop ran
    (idx 0..4 printed) but nothing downstream of the Location check
    ever executed, so annotate_variant() was never called once.

    Instead: manually find where the metadata lines end and the
    real header line is, skip exactly that many lines, and pass
    the parsed header in via `names=`.
    """
    header_comment_lines = []
    n_lines_to_skip = 0
    data_header = None

    with open(tsv_path, "r") as f:
        for line in f:
            if line.startswith("##"):
                header_comment_lines.append(line)
                n_lines_to_skip += 1
                continue

            if line.startswith("#"):
                data_header = line.lstrip("#").rstrip("\n").split("\t")
                n_lines_to_skip += 1

            break

    if data_header is None:
        raise ValueError(
            f"Could not find VEP TSV column header (line starting with a "
            f"single '#') in {tsv_path}"
        )

    df = pd.read_csv(
        tsv_path,
        sep="\t",
        skiprows=n_lines_to_skip,
        names=data_header,
        low_memory=False,
    )

    return header_comment_lines, df


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
    # filtered_vcf is no longer needed here - we used to bcftools-query it
    # to build a REF lookup keyed off VEP's Location+Allele, which silently
    # dropped indels (see docstring in the annotation loop below). Now we
    # parse chrom/pos/ref/alt straight from Uploaded_variation instead.

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

    # Step 3: Read VEP TSV (see _read_vep_tsv docstring for why this isn't
    # a plain pd.read_csv(..., comment="#") call)
    logger.info(f"[{session_id}] Reading VEP TSV...")

    header_lines, df = _read_vep_tsv(partial_tsv_path)

    original_row_count = len(df)
    logger.info(f"[{session_id}] Loaded {original_row_count:,} rows")

    # Sanity check: fail loudly instead of silently producing 0 annotations
    # if the columns we depend on aren't present.
    required_columns = ["Uploaded_variation", "Feature", "VARIANT_CLASS"]
    missing_required = [c for c in required_columns if c not in df.columns]
    if missing_required:
        logger.error(
            f"[{session_id}] VEP TSV is missing required columns: "
            f"{missing_required}. Actual columns: {df.columns.tolist()}"
        )
        raise ValueError(
            f"VEP TSV missing required columns {missing_required} - "
            f"cannot annotate. Got columns: {df.columns.tolist()}"
        )

    logger.info(f"[{session_id}] [DEBUG] Columns: {df.columns.tolist()}")
    logger.info(f"[{session_id}] [DEBUG] First row:\n{df.iloc[0]}")

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

    # NOTE: We used to build a (chrom,pos,alt) -> ref lookup from the
    # filtered VCF via bcftools, keyed off VEP's Location+Allele columns.
    # That silently dropped indels: VEP's Location/Allele are normalized
    # (deletions get position-shifted and ALT rendered as "-"), so the
    # lookup key built from Location/Allele didn't match the raw VCF key
    # built from bcftools for any indel. Confirmed empirically - e.g. a
    # deletion at chr1:923311 (TG>T) showed up in VEP's own output as
    # Location=923312, Allele="-".
    #
    # Fix: vep_runner.py now blanks the VCF ID column before running VEP,
    # which forces VEP's Uploaded_variation column to be a literal,
    # unshifted echo of "chrom_pos_ref/alt" from the input line (VEP only
    # uses a real ID column there when one is present; ours are blanked).
    # We parse chrom/pos/ref/alt straight from that instead - no VCF
    # re-lookup needed at all, and no indel mismatch.

    annotated_count = 0

    missing_uploaded_variation = 0

    for idx, row in df.iterrows():
        if idx < 5:
            logger.info(f"[DEBUG LOOP] Processing dataframe row {idx}")

        uploaded = str(row.get("Uploaded_variation", ""))

        # Expected format: "chrom_pos_ref/alt", e.g. "chr1_923311_TG/T"
        # maxsplit=2 protects against any stray underscores in contig names
        parts = uploaded.split("_", 2)
        if len(parts) != 3 or "/" not in parts[2]:
            missing_uploaded_variation += 1
            if missing_uploaded_variation <= 10:
                logger.warning(
                    f"[{session_id}] Row {idx}: unparseable Uploaded_variation "
                    f"{uploaded!r} - expected 'chrom_pos_ref/alt' "
                    f"(is the VCF ID column blanked before VEP?)"
                )
            continue

        chrom_part, pos_part, ref_alt_part = parts
        chrom = chrom_part.removeprefix("chr")

        try:
            pos = int(pos_part)
        except ValueError:
            if idx < 5:
                logger.warning(f"[DEBUG] Row {idx} has unparseable pos: {pos_part!r}")
            continue

        ref, alt = ref_alt_part.split("/", 1)

        if idx < 5:
            logger.info(
                "[DEBUG PARSED] uploaded=%r -> chrom=%r pos=%r ref=%r alt=%r",
                uploaded, chrom, pos, ref, alt,
            )

        # Transcript ID from Feature column
        transcript_id = str(row.get("Feature", ""))

        # Variant class from VARIANT_CLASS column
        variant_class = str(row.get("VARIANT_CLASS", "SNV"))

        if idx == 0:
            logger.info(
                f"[DEBUG] First VEP row: "
                f"{chrom}:{pos} {ref}>{alt} "
                f"transcript={transcript_id} "
                f"variant_class={variant_class}"
            )

        if not all([chrom, pos, ref, alt]):
            continue

        if not hasattr(annotator, "_input_debug"):
            annotator._input_debug = 0

        if annotator._input_debug < 5:
            logger.info(
                "[DEBUG INPUT] "
                "CHROM=%r POS=%r REF=%r ALT=%r "
                "Transcript=%r VariantClass=%r",
                chrom,
                pos,
                ref,
                alt,
                transcript_id,
                variant_class,
            )
            annotator._input_debug += 1

        # Lookup annotations
        try:
            if idx < 5:
                logger.info("[DEBUG] Calling annotate_variant()")

            annotations = annotator.annotate_variant(
                chrom=chrom,
                pos=pos,
                ref=ref,
                alt=alt,
                transcript_id=transcript_id,
                variant_class=variant_class
            )

            if idx < 5:
                logger.info(
                    "[DEBUG] annotate_variant returned %d fields: %s",
                    len(annotations),
                    list(annotations.keys()),
                )

            for col, value in annotations.items():
                if col in df.columns:
                    df.at[idx, col] = value

            if annotations:
                annotated_count += 1

        except Exception as e:
            logger.warning(f"[{session_id}] Failed to annotate row {idx}: {e}", exc_info=(idx < 5))
            continue

    logger.info(f"[{session_id}] Annotated {annotated_count}/{original_row_count} rows")

    logger.info(
        f"[{session_id}] Unparseable Uploaded_variation for {missing_uploaded_variation:,} rows"
    )

    # Step 6: Write complete TSV
    complete_tsv_path = partial_tsv_path.parent / f"{session_id}_vep_complete.tsv"

    logger.info(f"[{session_id}] Writing complete TSV to {complete_tsv_path.name}")

    with open(complete_tsv_path, "w") as f:
        f.writelines(header_lines)
        df.to_csv(f, sep="\t", index=False)

    logger.info(f"[{session_id}] ✓ Complete TSV written: {complete_tsv_path}")

    return {
        "annotated_tsv": str(complete_tsv_path),
    }
