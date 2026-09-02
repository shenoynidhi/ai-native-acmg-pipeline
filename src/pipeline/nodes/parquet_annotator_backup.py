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

        # Build O(1) hash indexes so per-variant lookup doesn't mean
        # re-scanning the whole multi-million-row frame every call.
        # This is the actual bottleneck once the header bug was fixed:
        # a boolean mask over 8.7M object-dtype rows costs ~0.5-0.7s,
        # and that was happening once per VEP row (83k+ times) -> ~17hrs.
        # Indexing trades that for one O(n) pass at load time and O(1)
        # dict lookups after that.
        logger.info("[ParquetAnnotator] Building ClinVar hash index...")
        self._clinvar_index, self._clinvar_col_idx = self._build_index(
            self.clinvar_df, ["CHROM", "POS", "REF", "ALT"], multi=False
        )
        logger.info(
            f"[ParquetAnnotator] ClinVar index: {len(self._clinvar_index):,} unique keys"
        )

        logger.info("[ParquetAnnotator] Building dbNSFP hash index...")
        self._dbnsfp_index, self._dbnsfp_col_idx = self._build_index(
            self.dbnsfp_df, ["chr", "pos", "ref", "alt"], multi=True
        )
        logger.info(
            f"[ParquetAnnotator] dbNSFP index: {len(self._dbnsfp_index):,} unique keys"
        )

        # SpliceAI is TOO LARGE to load into memory (76 GB total)
        # Store paths for on-demand lookup instead
        self.spliceai_snv_path = self._get_spliceai_path("snv")
        self.spliceai_indel_path = self._get_spliceai_path("indel")

        logger.info("[ParquetAnnotator] All databases loaded successfully (SpliceAI on-demand)")


    def _load_clinvar(self) -> pd.DataFrame:
        """Load ClinVar parquet with chromosome filter."""
        path = PARQUET_BASE / "clinvar_parquet" / f"grch{self.build_suffix}" / f"clinvar{self.build_suffix}.parquet"

        if not path.exists():
            logger.warning(f"ClinVar parquet not found: {path}")
            return pd.DataFrame()

        logger.info(f"Loading ClinVar from {path}")

        df = pd.read_parquet(
            path,
            filters=[("CHROM", "in", list(self.chromosomes_needed))]
        )

        logger.info(f"✓ ClinVar loaded: {len(df):,} rows")
        logger.info(
            f"[DEBUG] ClinVar dtypes:\n"
            f"{df[['CHROM','POS','REF','ALT']].dtypes}"
        )
        logger.info(
            "[DEBUG] ClinVar first row:\n"
            f"{df[['CHROM','POS','REF','ALT']].head(1)}"
        )
        logger.info(
            "[DEBUG] ClinVar chromosome values (sample): %s",
            sorted(df["CHROM"].drop_duplicates().tolist())[:30]
        )

        logger.info(
            "[DEBUG] ClinVar first row repr: "
            "CHROM=%r POS=%r REF=%r ALT=%r",
            df.iloc[0]["CHROM"],
            df.iloc[0]["POS"],
            df.iloc[0]["REF"],
            df.iloc[0]["ALT"],
        )
        return df


    def _load_dbnsfp(self) -> pd.DataFrame:
        """Load dbNSFP parquet with chromosome filter."""
        path = PARQUET_BASE / "dbnsfp_parquet" / f"grch{self.build_suffix}" / f"dbnsfp{self.build_suffix}.parquet"

        if not path.exists():
            logger.warning(f"dbNSFP parquet not found: {path}")
            return pd.DataFrame()

        logger.info(f"Loading dbNSFP from {path}")

        df = pd.read_parquet(
            path,
            filters=[("chr", "in", list(self.chromosomes_needed))]
        )

        logger.info(f"✓ dbNSFP loaded: {len(df):,} rows")
        logger.info(
            f"[DEBUG] dbNSFP dtypes:\n"
            f"{df[['chr','pos','ref','alt']].dtypes}"
        )
        logger.info(
            "[DEBUG] dbNSFP first row:\n"
            f"{df[['chr','pos','ref','alt']].head(1)}"
        )
        return df


    @staticmethod
    def _build_index(df: pd.DataFrame, key_cols: List[str], multi: bool):
        """
        Build a hash index over `df` keyed by `key_cols`, plus a
        column-name -> tuple-position map for retrieving other fields.

        We deliberately use plain tuples (itertuples(name=None)) rather
        than namedtuples here. namedtuples derive attribute names from
        column labels, and dbNSFP has a column called "GERP++_RS" - "+"
        is not a legal identifier character, so pandas silently renames
        that field to a positional name like "_7". getattr(row,
        "GERP++_RS", "") would then always fall through to the default
        and return "" forever, with no error - the same class of silent
        failure as the earlier header-parsing bug, just one layer deeper.
        Plain tuples + an explicit name->index map avoid the identifier
        restriction entirely.

        Args:
            multi: if True, each key maps to a list of rows (dbNSFP can
                   have >1 row per (chr,pos,ref,alt)). If False, each key
                   maps to a single row (first one wins), like ClinVar.

        Returns:
            (index, col_idx) where col_idx maps column name -> position
            in each stored row tuple.
        """
        col_idx = {c: i for i, c in enumerate(df.columns)}

        if df.empty:
            return {}, col_idx

        key_positions = [col_idx[c] for c in key_cols]
        index: Dict[tuple, object] = {}

        for row in df.itertuples(index=False, name=None):
            key = tuple(row[i] for i in key_positions)
            if multi:
                index.setdefault(key, []).append(row)
            else:
                if key not in index:
                    index[key] = row

        return index, col_idx

    def _get_spliceai_path(self, variant_type: str) -> Path:
        """
        Get path to SpliceAI chunks directory.

        Args:
            variant_type: "snv" or "indel"

        Returns:
            Path to chunks directory
        """
        chunks_dir = PARQUET_BASE / "spliceai_parquet" / f"grch{self.build_suffix}" / variant_type / "chunks"

        if not chunks_dir.exists():
            logger.warning(f"SpliceAI {variant_type} chunks not found: {chunks_dir}")
            return None

        return chunks_dir


    def lookup_clinvar(self, chrom: str, pos: int, ref: str, alt: str) -> Dict[str, str]:
        """
        Lookup ClinVar annotation for a variant.

        Returns dict with keys: CLNSIG, CLNREVSTAT, CLNDN
        """
        if not self._clinvar_index:
            return {}

        if not hasattr(self, "_mask_debug"):
            self._mask_debug = 0

        row = self._clinvar_index.get((chrom, pos, ref, alt))

        if self._mask_debug < 5:
            logger.info(
                "[DEBUG ClinVar] %s:%s %s>%s match=%s",
                chrom, pos, ref, alt, row is not None,
            )
            self._mask_debug += 1

        if row is None:

            if not hasattr(self, "_clinvar_debug"):
                self._clinvar_debug = 0

            if self._clinvar_debug < 5:
                # Cheap only because this branch is capped at 5 calls total -
                # a full mask scan here is fine, it's the unconditional
                # per-row version that was the problem.
                same_pos = self.clinvar_df[
                    (self.clinvar_df["CHROM"] == chrom) &
                    (self.clinvar_df["POS"] == pos)
                ]

                logger.info(
                    "[DEBUG CLINVAR MISS] Looking for %r:%r %r>%r",
                    chrom, pos, ref, alt,
                )
                logger.info(
                    "[DEBUG CLINVAR MISS] Rows at same position: %d",
                    len(same_pos),
                )

                if not same_pos.empty:
                    logger.info(
                        "[DEBUG CLINVAR MISS] Matching position rows:\n%s",
                        same_pos[["CHROM", "POS", "REF", "ALT"]].head(10)
                    )

                self._clinvar_debug += 1

            return {}

        ci = self._clinvar_col_idx
        return {
            "ClinVar_CLNSIG": str(row[ci["CLNSIG"]]) if "CLNSIG" in ci else "",
            "ClinVar_CLNREVSTAT": str(row[ci["CLNREVSTAT"]]) if "CLNREVSTAT" in ci else "",
            "ClinVar_CLNDN": str(row[ci["CLNDN"]]) if "CLNDN" in ci else "",
        }


    def lookup_dbnsfp(self, chrom: str, pos: int, ref: str, alt: str, transcript_id: str) -> Dict[str, str]:
        """
        Lookup dbNSFP scores for a variant.

        Handles multi-transcript rows (semicolon-separated values).
        Matches on (chr, pos, ref, alt, transcript_id).

        Returns dict with score column names as keys.
        """
        if not self._dbnsfp_index:
            return {}

        matches = self._dbnsfp_index.get((chrom, pos, ref, alt), [])

        if not hasattr(self, "_db_mask_debug"):
            self._db_mask_debug = 0

        if self._db_mask_debug < 5:
            logger.info(
                "[DEBUG dbNSFP] %s:%s %s>%s matches=%d",
                chrom, pos, ref, alt, len(matches),
            )
            self._db_mask_debug += 1

        if not matches:

            if not hasattr(self, "_dbnsfp_debug"):
                self._dbnsfp_debug = 0

            if self._dbnsfp_debug < 5:
                # Only runs for the first 5 misses total - a full mask
                # scan here is acceptable, unlike doing it every row.
                same_pos = self.dbnsfp_df[
                    (self.dbnsfp_df["chr"] == chrom) &
                    (self.dbnsfp_df["pos"] == pos)
                ]

                logger.info(
                    "[DEBUG DBNSFP MISS] Looking for %r:%r %r>%r",
                    chrom, pos, ref, alt,
                )
                logger.info(
                    "[DEBUG DBNSFP MISS] Rows at same position: %d",
                    len(same_pos),
                )

                if not same_pos.empty:
                    logger.info(
                        "[DEBUG DBNSFP MISS] Matching position rows:\n%s",
                        same_pos[["chr", "pos", "ref", "alt"]].head(10)
                    )

                self._dbnsfp_debug += 1

            return {}

        result = {}
        score_columns = [
            "REVEL_score", "CADD_phred", "Polyphen2_HDIV_score", "SIFT_score",
            "phyloP100way_vertebrate", "GERP++_RS", "MutationTaster_pred", "MetaSVM_score"
        ]
        di = self._dbnsfp_col_idx

        for row in matches:
            if "Ensembl_transcriptid" not in di:
                continue
            transcript_str = str(row[di["Ensembl_transcriptid"]])
            if not transcript_str or transcript_str == "nan":
                continue

            transcripts = transcript_str.split(";")

            try:
                idx = transcripts.index(transcript_id)

                if not hasattr(self, "_transcript_hit_debug"):
                    self._transcript_hit_debug = 0

                if self._transcript_hit_debug < 5:
                    logger.info(
                        "[DEBUG] Transcript MATCH %s index=%d",
                        transcript_id, idx,
                    )
                    self._transcript_hit_debug += 1

            except ValueError:

                if not hasattr(self, "_transcript_debug"):
                    self._transcript_debug = 0

                if self._transcript_debug < 5:
                    logger.info(
                        f"[DEBUG] Transcript mismatch. "
                        f"Looking for '{transcript_id}' in '{transcript_str}'"
                    )
                    self._transcript_debug += 1

                continue

            for col in score_columns:
                score_str = str(row[di[col]]) if col in di else ""
                if not score_str or score_str == "nan":
                    result[col] = ""
                    continue

                scores = score_str.split(";")
                if idx < len(scores):
                    result[col] = scores[idx]
                else:
                    result[col] = ""

            break

        return result


    def lookup_spliceai(self, chrom: str, pos: int, ref: str, alt: str, variant_class: str) -> Dict[str, str]:
        """
        Lookup SpliceAI scores for a variant using ON-DEMAND parquet read.
        """
        is_snv = variant_class == "SNV"
        chunks_dir = self.spliceai_snv_path if is_snv else self.spliceai_indel_path

        if not chunks_dir:
            return {}

        chunk_idx = SPLICEAI_CHUNK_MAP.get(chrom)
        if chunk_idx is None:
            return {}

        chunk_file = chunks_dir / f"chunk_{chunk_idx:03d}.parquet"
        if not chunk_file.exists():
            return {}

        try:
            df = pd.read_parquet(
                chunk_file,
                filters=[
                    ("CHROM", "==", chrom),
                    ("POS", "==", pos),
                    ("REF", "==", ref),
                    ("ALT", "==", alt),
                ]
            )
        except Exception as e:
            logger.warning(f"Failed to query SpliceAI chunk {chunk_file.name}: {e}")
            return {}

        if df.empty:
            return {}

        row = df.iloc[0]

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
        if not hasattr(self, "_annotate_debug"):
            self._annotate_debug = 0

        if self._annotate_debug < 5:
            logger.info(
                "[DEBUG annotate_variant] %s:%s %s>%s transcript=%s class=%s",
                chrom, pos, ref, alt, transcript_id, variant_class,
            )
            self._annotate_debug += 1

        annotations = {}

        annotations.update(self.lookup_clinvar(chrom, pos, ref, alt))
        annotations.update(self.lookup_dbnsfp(chrom, pos, ref, alt, transcript_id))
        annotations.update(self.lookup_spliceai(chrom, pos, ref, alt, variant_class))

        return annotations
