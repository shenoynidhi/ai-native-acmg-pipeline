"""
src/pipeline/nodes/parquet_annotator.py

Parquet-based annotation lookup for ClinVar, dbNSFP, and SpliceAI.
Replaces VEP plugin-based annotation with fast parquet lookups.

Memory-optimized: Only loads chromosomes present in the VCF.
"""

import logging
import pandas as pd
from pathlib import Path
from typing import Dict, List, Set, Optional

logger = logging.getLogger(__name__)

# Base path for parquet databases
PARQUET_BASE = Path("/mnt/ebs-databases/vep_databases")

# Chromosome to chunk mapping (SpliceAI is organized by chromosome)
# chunk_000 = chr1, chunk_001 = chr2, ..., chunk_021 = chr22, chunk_022 = chrX, chunk_023 = chrY, chunk_024 = chrM
SPLICEAI_CHUNK_MAP = {
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6, "8": 7, "9": 8, "10": 9,
    "11": 10, "12": 11, "13": 12, "14": 13, "15": 14, "16": 15, "17": 16, "18": 17, "19": 18, "20": 19,
    "21": 20, "22": 21, "X": 22, "Y": 23, "MT": 24, "M": 24
}


class ParquetAnnotator:
    """
    Fast parquet-based annotation lookup.

    Memory-optimized: Only loads data for chromosomes present in the VCF.
    """

    def __init__(self, genome_build: str, chromosomes_needed: Set[str]):
        """
        Initialize annotator and load parquet data for needed chromosomes only.

        Args:
            genome_build: "GRCh37" or "GRCh38"
            chromosomes_needed: Set of chromosome names present in VCF (e.g. {"1", "17", "X"})
        """
        self.genome_build = genome_build
        self.build_suffix = "37" if genome_build.upper() == "GRCH37" else "38"
        self.chromosomes_needed = chromosomes_needed

        logger.info(
            f"[ParquetAnnotator] Loading annotations for {genome_build}, "
            f"chromosomes: {sorted(chromosomes_needed)}"
        )

        # Load databases (chromosome-filtered)
        self.clinvar_df = self._load_clinvar()
        self.dbnsfp_df = self._load_dbnsfp()
        self.spliceai_snv_df = self._load_spliceai("snv")
        self.spliceai_indel_df = self._load_spliceai("indel")

        logger.info("[ParquetAnnotator] All databases loaded successfully")


    def _load_clinvar(self) -> pd.DataFrame:
        """Load ClinVar parquet with chromosome filter."""
        path = PARQUET_BASE / "clinvar_parquet" / f"grch{self.build_suffix}" / f"clinvar{self.build_suffix}.parquet"

        if not path.exists():
            logger.warning(f"ClinVar parquet not found: {path}")
            return pd.DataFrame()

        logger.info(f"Loading ClinVar from {path}")

        # Load with chromosome filter (pyarrow filters read only matching rows from disk)
        df = pd.read_parquet(
            path,
            filters=[("CHROM", "in", list(self.chromosomes_needed))]
        )

        logger.info(f"✓ ClinVar loaded: {len(df):,} rows")
        return df


    def _load_dbnsfp(self) -> pd.DataFrame:
        """Load dbNSFP parquet with chromosome filter."""
        path = PARQUET_BASE / "dbnsfp_parquet" / f"grch{self.build_suffix}" / f"dbnsfp{self.build_suffix}.parquet"

        if not path.exists():
            logger.warning(f"dbNSFP parquet not found: {path}")
            return pd.DataFrame()

        logger.info(f"Loading dbNSFP from {path}")

        # Load with chromosome filter
        df = pd.read_parquet(
            path,
            filters=[("chr", "in", list(self.chromosomes_needed))]
        )

        logger.info(f"✓ dbNSFP loaded: {len(df):,} rows")
        return df


    def _load_spliceai(self, variant_type: str) -> pd.DataFrame:
        """
        Load SpliceAI parquet chunks for needed chromosomes only.

        Args:
            variant_type: "snv" or "indel"
        """
        chunks_dir = PARQUET_BASE / "spliceai_parquet" / f"grch{self.build_suffix}" / variant_type / "chunks"

        if not chunks_dir.exists():
            logger.warning(f"SpliceAI {variant_type} chunks not found: {chunks_dir}")
            return pd.DataFrame()

        # Determine which chunks to load based on chromosomes needed
        chunks_to_load = []
        for chrom in self.chromosomes_needed:
            chunk_idx = SPLICEAI_CHUNK_MAP.get(chrom)
            if chunk_idx is not None:
                chunk_file = chunks_dir / f"chunk_{chunk_idx:03d}.parquet"
                if chunk_file.exists():
                    chunks_to_load.append(chunk_file)

        if not chunks_to_load:
            logger.warning(f"No SpliceAI {variant_type} chunks found for chromosomes {self.chromosomes_needed}")
            return pd.DataFrame()

        logger.info(f"Loading SpliceAI {variant_type}: {len(chunks_to_load)} chunks")

        # Load and concatenate chunks
        dfs = []
        for chunk_file in chunks_to_load:
            df_chunk = pd.read_parquet(chunk_file)
            dfs.append(df_chunk)
            logger.debug(f"  Loaded {chunk_file.name}: {len(df_chunk):,} rows")

        df = pd.concat(dfs, ignore_index=True)
        logger.info(f"✓ SpliceAI {variant_type} loaded: {len(df):,} rows from {len(chunks_to_load)} chunks")

        return df


    def lookup_clinvar(self, chrom: str, pos: int, ref: str, alt: str) -> Dict[str, str]:
        """
        Lookup ClinVar annotation for a variant.

        Returns dict with keys: CLNSIG, CLNREVSTAT, CLNDN
        """
        if self.clinvar_df.empty:
            return {}

        # Query for exact match
        mask = (
            (self.clinvar_df["CHROM"] == chrom) &
            (self.clinvar_df["POS"] == pos) &
            (self.clinvar_df["REF"] == ref) &
            (self.clinvar_df["ALT"] == alt)
        )

        matches = self.clinvar_df[mask]

        if matches.empty:
            return {}

        # Take first match (there may be multiple submissions)
        row = matches.iloc[0]

        return {
            "ClinVar_CLNSIG": str(row.get("CLNSIG", "")),
            "ClinVar_CLNREVSTAT": str(row.get("CLNREVSTAT", "")),
            "ClinVar_CLNDN": str(row.get("CLNDN", "")),
        }


    def lookup_dbnsfp(self, chrom: str, pos: int, ref: str, alt: str, transcript_id: str) -> Dict[str, str]:
        """
        Lookup dbNSFP scores for a variant.

        Handles multi-transcript rows (semicolon-separated values).
        Matches on (chr, pos, ref, alt, transcript_id).

        Returns dict with score column names as keys.
        """
        if self.dbnsfp_df.empty:
            return {}

        # Query for exact match
        mask = (
            (self.dbnsfp_df["chr"] == chrom) &
            (self.dbnsfp_df["pos"] == pos) &
            (self.dbnsfp_df["ref"] == ref) &
            (self.dbnsfp_df["alt"] == alt)
        )

        matches = self.dbnsfp_df[mask]

        if matches.empty:
            return {}

        # dbNSFP may have multiple rows per variant (different transcripts)
        # Ensembl_transcriptid format: "ENST00000568584;ENST00000564130;ENST00000568866"
        # Score format: ".;.;." or "0.5;0.6;0.7"

        result = {}
        score_columns = [
            "REVEL_score", "CADD_phred", "Polyphen2_HDIV_score", "SIFT_score",
            "phyloP100way_vertebrate", "GERP++_RS", "MutationTaster_pred", "MetaSVM_score"
        ]

        for _, row in matches.iterrows():
            # Get transcript list
            transcript_str = str(row.get("Ensembl_transcriptid", ""))
            if not transcript_str or transcript_str == "nan":
                continue

            transcripts = transcript_str.split(";")

            # Find index of our transcript
            try:
                idx = transcripts.index(transcript_id)
            except ValueError:
                # Transcript not in this row, try next
                continue

            # Extract score at this index for each column
            for col in score_columns:
                score_str = str(row.get(col, ""))
                if not score_str or score_str == "nan":
                    result[col] = ""
                    continue

                scores = score_str.split(";")
                if idx < len(scores):
                    result[col] = scores[idx]
                else:
                    result[col] = ""

            # Found matching transcript, stop searching
            break

        return result


    def lookup_spliceai(self, chrom: str, pos: int, ref: str, alt: str, variant_class: str) -> Dict[str, str]:
        """
        Lookup SpliceAI scores for a variant.

        Args:
            chrom, pos, ref, alt: Variant coordinates
            variant_class: VEP variant class (e.g. "SNV", "insertion", "deletion")

        Returns dict with key "SpliceAI_pred" formatted as VEP plugin output:
            Format: SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL
        """
        # Determine which dataframe to use (SNV vs INDEL)
        is_snv = variant_class == "SNV"
        df = self.spliceai_snv_df if is_snv else self.spliceai_indel_df

        if df.empty:
            return {}

        # Query for exact match
        mask = (
            (df["CHROM"] == chrom) &
            (df["POS"] == pos) &
            (df["REF"] == ref) &
            (df["ALT"] == alt)
        )

        matches = df[mask]

        if matches.empty:
            return {}

        # Take first match
        row = matches.iloc[0]

        # Format as VEP plugin output: SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL
        symbol = str(row.get("SYMBOL", ""))
        ds_ag = row.get("DS_AG", 0.0)
        ds_al = row.get("DS_AL", 0.0)
        ds_dg = row.get("DS_DG", 0.0)
        ds_dl = row.get("DS_DL", 0.0)
        dp_ag = int(row.get("DP_AG", 0))
        dp_al = int(row.get("DP_AL", 0))
        dp_dg = int(row.get("DP_DG", 0))
        dp_dl = int(row.get("DP_DL", 0))

        spliceai_pred = f"{symbol}|{ds_ag:.2f}|{ds_al:.2f}|{ds_dg:.2f}|{ds_dl:.2f}|{dp_ag}|{dp_al}|{dp_dg}|{dp_dl}"

        return {"SpliceAI_pred": spliceai_pred}


    def annotate_variant(
        self,
        chrom: str,
        pos: int,
        ref: str,
        alt: str,
        transcript_id: str,
        variant_class: str
    ) -> Dict[str, str]:
        """
        Lookup all annotations for a single variant.

        Returns merged dict with all annotation fields.
        """
        annotations = {}

        # ClinVar
        annotations.update(self.lookup_clinvar(chrom, pos, ref, alt))

        # dbNSFP (transcript-aware)
        annotations.update(self.lookup_dbnsfp(chrom, pos, ref, alt, transcript_id))

        # SpliceAI
        annotations.update(self.lookup_spliceai(chrom, pos, ref, alt, variant_class))

        return annotations

