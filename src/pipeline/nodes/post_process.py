"""
src/pipeline/nodes/post_process.py

Post-Process Node — Phase 4
Parses VEP tab-delimited output and populates VariantState fields for
Phases 1–6 (variant identity, population frequency, ClinVar, in-silico
scores, structural flags, gene-level context).

Also performs gene-level lookups against:
- gnomAD constraint  (pLI, LOEUF, Z-score)
- ClinGen validity   (gene-disease classification)
- HGNC               (gene symbol normalisation)

One call to this node processes ALL variants in the VEP TSV and returns
a list of fully-populated VariantState dicts — one per canonical variant.
The graph then fans out to run agents on each variant independently.

VEP TSV column reference (--everything --tab --canonical output):
Uploaded_variation, Location, Allele, Gene, Feature, Feature_type,
Consequence, cDNA_position, CDS_position, Protein_position,
Amino_acids, Codons, Existing_variation, IMPACT, DISTANCE, STRAND,
FLAGS, SYMBOL, SYMBOL_SOURCE, HGNC_ID, CANONICAL, SOURCE, EXON,
INTRON, HGVSc, HGVSp, HGVS_OFFSET,
gnomADe_AF, gnomADe_AFR_AF, ..., gnomADe_SAS_AF,
CADD_phred, GERP++_RS, Polyphen2_HDIV_score, REVEL_score, SIFT_score,
phyloP100way_vertebrate, SpliceAI_pred,
LoF, LoF_filter, LoF_flags, LoF_info,
ClinVar, ClinVar_CLNSIG, ClinVar_CLNREVSTAT, ClinVar_CLNDN, ClinVar_CLNACC
"""

import csv
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.config import DATABASE_PATHS, OUTPUT_DIR
from src.pipeline.state import VariantState, build_initial_state
import gzip as _gzip

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    logger.warning("pandas not available - VEP TSV parsing will be slower")

try:
    from cyvcf2 import VCF
    CYVCF2_AVAILABLE = True
except ImportError:
    CYVCF2_AVAILABLE = False

try:
    import pysam
    PYSAM_AVAILABLE = True
except ImportError:
    PYSAM_AVAILABLE = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parental genotype extraction (trio mode)
# ---------------------------------------------------------------------------

def _extract_parental_genotype(
    vcf_path: str,
    chrom: str,
    pos: int,
    ref: str,
    alt: str
) -> Optional[str]:
    """
    Extract genotype (GT field) from a parental VCF at a specific variant position.

    Args:
        vcf_path: Path to parent VCF file
        chrom: Chromosome (e.g., "13" or "chr13")
        pos: 1-based position
        ref: Reference allele
        alt: Alternate allele

    Returns:
        Genotype string: "0/0", "0/1", "1/1", "0|1", etc.
        "0/0" if variant not found in parent VCF (assumes reference)
        None if VCF cannot be read
    """
    if not PYSAM_AVAILABLE:
        logger.warning("pysam not available - cannot extract parental genotypes")
        return None

    if not vcf_path or not Path(vcf_path).exists():
        logger.warning(f"Parent VCF not found: {vcf_path}")
        return None

    try:
        # Normalize chromosome name (strip "chr" prefix if present in query)
        chrom_normalized = chrom.replace("chr", "")

        vcf = pysam.VariantFile(vcf_path)

        # Try both with and without "chr" prefix
        for chrom_variant in [chrom, chrom_normalized, f"chr{chrom_normalized}"]:
            try:
                # Fetch region (pos-1 to pos for 0-based pysam)
                for record in vcf.fetch(chrom_variant, pos-1, pos):
                    # Check if this is the exact variant we're looking for
                    if record.pos != pos:
                        continue

                    if record.ref != ref:
                        continue

                    # Check if alt allele matches
                    if alt not in [str(a) for a in record.alts or []]:
                        continue

                    # Found matching variant - extract GT
                    if len(record.samples) == 0:
                        logger.warning(f"No samples in parent VCF at {chrom}:{pos}")
                        return None

                    sample = record.samples[0]  # Assume single-sample VCF
                    gt = sample.get("GT")

                    if gt is None:
                        logger.warning(f"No GT field for variant {chrom}:{pos} in {vcf_path}")
                        return "0/0"  # Missing GT → assume reference

                    # Format genotype: (0, 1) → "0/1" or "0|1" based on phasing
                    if sample.phased:
                        return "|".join(str(allele) if allele is not None else "." for allele in gt)
                    else:
                        return "/".join(str(allele) if allele is not None else "." for allele in gt)

            except Exception as e:
                # Try next chromosome variant
                continue

        # Variant not found in parent VCF → assume homozygous reference
        logger.debug(f"Variant {chrom}:{pos} not found in {Path(vcf_path).name} - assuming 0/0")
        return "0/0"

    except Exception as e:
        logger.warning(f"Error extracting genotype from {vcf_path} at {chrom}:{pos}: {e}")
        return None


# ---------------------------------------------------------------------------
# gnomAD population AF columns in VEP TSV output
# GRCh38 v4.1 uses AF_ami, AF_mid, AF_remaining
# GRCh37 v2.1.1 uses AF_oth (not AF_remaining) and lacks AF_ami/AF_mid
# ---------------------------------------------------------------------------
_GNOMAD_POP_COLS = {
    "afr": "gnomAD_AF_afr",
    "ami": "gnomAD_AF_ami",        # GRCh38 only
    "amr": "gnomAD_AF_amr",
    "asj": "gnomAD_AF_asj",
    "eas": "gnomAD_AF_eas",
    "fin": "gnomAD_AF_fin",
    "mid": "gnomAD_AF_mid",        # GRCh38 only
    "nfe": "gnomAD_AF_nfe",
    "remaining": "gnomAD_AF_remaining",  # GRCh38
    "oth": "gnomAD_AF_oth",        # GRCh37 equivalent of "remaining"
    "sas": "gnomAD_AF_sas",
}

# Consequence types used for structural flags
_LOF_CONSEQUENCES = {
    "stop_gained", "frameshift_variant", "splice_acceptor_variant",
    "splice_donor_variant", "start_lost", "stop_lost",
    "transcript_ablation", "transcript_amplification",
}
_INFRAME_CONSEQUENCES = {
    "inframe_insertion", "inframe_deletion",
    "protein_altering_variant",
}

# ClinVar significance → star count mapping (CLNREVSTAT)
_CLNREVSTAT_STARS = {
    "practice_guideline": 4,
    "reviewed_by_expert_panel": 3,
    "criteria_provided,_multiple_submitters,_no_conflicts": 2,
    "criteria_provided,_conflicting_classifications": 1,
    "criteria_provided,_single_submitter": 1,
    "no_assertion_criteria_provided": 0,
    "no_classification_provided": 0,
    "no_classification_for_the_single_variant": 0,
}


# ---------------------------------------------------------------------------
# Genomic HGVS generation (fallback when HGVSc is blank)
# ---------------------------------------------------------------------------

def _generate_genomic_hgvs(chrom: str, pos: int, ref: str, alt: str, genome_build: str = "GRCh38") -> str:
    """
    Generate genomic HGVS notation (NC_ accession) for variants missing HGVSc.

    Args:
        chrom: Chromosome (e.g., "7" or "chr7")
        pos: Position (1-based)
        ref: Reference allele
        alt: Alternate allele
        genome_build: "GRCh38" or "GRCh37"

    Returns:
        Genomic HGVS string like "NC_000007.14:g.117548628A>G"
    """
    # RefSeq chromosome accessions (GRCh38/hg38)
    CHROM_ACCESSIONS_38 = {
        "1": "NC_000001.11", "2": "NC_000002.12", "3": "NC_000003.12",
        "4": "NC_000004.12", "5": "NC_000005.10", "6": "NC_000006.12",
        "7": "NC_000007.14", "8": "NC_000008.11", "9": "NC_000009.12",
        "10": "NC_000010.11", "11": "NC_000011.10", "12": "NC_000012.12",
        "13": "NC_000013.11", "14": "NC_000014.9", "15": "NC_000015.10",
        "16": "NC_000016.10", "17": "NC_000017.11", "18": "NC_000018.10",
        "19": "NC_000019.10", "20": "NC_000020.11", "21": "NC_000021.9",
        "22": "NC_000022.11", "X": "NC_000023.11", "Y": "NC_000024.10",
        "MT": "NC_012920.1", "M": "NC_012920.1"
    }

    # RefSeq chromosome accessions (GRCh37/hg19)
    CHROM_ACCESSIONS_37 = {
        "1": "NC_000001.10", "2": "NC_000002.11", "3": "NC_000003.11",
        "4": "NC_000004.11", "5": "NC_000005.9", "6": "NC_000006.11",
        "7": "NC_000007.13", "8": "NC_000008.10", "9": "NC_000009.11",
        "10": "NC_000010.10", "11": "NC_000011.9", "12": "NC_000012.11",
        "13": "NC_000013.10", "14": "NC_000014.8", "15": "NC_000015.9",
        "16": "NC_000016.9", "17": "NC_000017.10", "18": "NC_000018.9",
        "19": "NC_000019.9", "20": "NC_000020.10", "21": "NC_000021.8",
        "22": "NC_000022.10", "X": "NC_000023.10", "Y": "NC_000024.9",
        "MT": "NC_012920.1", "M": "NC_012920.1"
    }

    accessions = CHROM_ACCESSIONS_37 if genome_build.upper() == "GRCH37" else CHROM_ACCESSIONS_38
    chrom_clean = chrom.replace("chr", "").upper()
    accession = accessions.get(chrom_clean, f"chr{chrom_clean}")

    # Determine HGVS type based on variant
    if len(ref) == 1 and len(alt) == 1:
        # SNV: g.117548628A>G
        return f"{accession}:g.{pos}{ref}>{alt}"
    elif len(ref) > len(alt):
        # Deletion
        if len(alt) == 1:  # Simple deletion
            del_start = pos + 1
            del_end = pos + len(ref) - 1
            if del_start == del_end:
                return f"{accession}:g.{del_start}del"
            else:
                return f"{accession}:g.{del_start}_{del_end}del"
        else:
            # Delins
            return f"{accession}:g.{pos}_{pos + len(ref) - 1}delins{alt}"
    elif len(alt) > len(ref):
        # Insertion
        if len(ref) == 1:  # Simple insertion
            return f"{accession}:g.{pos}_{pos + 1}ins{alt[1:]}"
        else:
            # Delins
            return f"{accession}:g.{pos}_{pos + len(ref) - 1}delins{alt}"
    else:
        # Same length delins
        return f"{accession}:g.{pos}_{pos + len(ref) - 1}delins{alt}"


# ---------------------------------------------------------------------------
# Zygosity extraction from VCF GT field
# ---------------------------------------------------------------------------

def _extract_zygosity_from_vcf(
    vcf_path: str,
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    proband_sex: str = "Unknown"
) -> Optional[str]:
    """
    Extract zygosity (het/hom/hemi) from VCF GT field for a specific variant.

    Uses a module-level cache to avoid reopening the VCF for every variant.
    This provides massive speedup when processing thousands of variants.

    Args:
        vcf_path: Path to VCF file (can be .vcf or .vcf.gz)
        chrom: Chromosome (e.g., "chr7" or "7")
        pos: Position (1-based)
        ref: Reference allele
        alt: Alternate allele
        proband_sex: "Male" or "Female" for X-chromosome hemizygous detection

    Returns:
        "heterozygous", "homozygous", "hemizygous", or None if not found/parseable
    """
    logger.debug(f"_extract_zygosity_from_vcf called: vcf={Path(vcf_path).name}, chrom={chrom}, pos={pos}")

    if not CYVCF2_AVAILABLE:
        logger.warning("cyvcf2 not available — cannot extract zygosity from VCF")
        return None

    global _vcf_reader_cache

    try:
        # Use cached VCF reader if available, otherwise open and cache
        if vcf_path not in _vcf_reader_cache:
            _vcf_reader_cache[vcf_path] = VCF(vcf_path)
            logger.debug(f"Opened and cached VCF reader for {Path(vcf_path).name}")

        vcf = _vcf_reader_cache[vcf_path]

        # Try region query first (fast for large VCFs)
        query_variants = []
        for chrom_to_try in [chrom, f"chr{chrom}" if not chrom.startswith("chr") else chrom.replace("chr", "")]:
            try:
                query_variants = list(vcf(f"{chrom_to_try}:{pos}-{pos}"))
                if query_variants:
                    break
            except:
                pass

        # Region query failed (likely missing contig headers) - fall back to iteration
        if not query_variants:
            logger.debug(f"Region query failed for {chrom}:{pos}, falling back to VCF iteration")
            vcf = VCF(vcf_path)  # Re-open for iteration
            for variant in vcf:
                # Normalize chromosome names for comparison
                var_chrom = variant.CHROM.replace("chr", "")
                query_chrom = chrom.replace("chr", "")

                if var_chrom == query_chrom and variant.POS == pos:
                    if variant.REF == ref and alt in [str(a) for a in variant.ALT]:
                        query_variants = [variant]
                        break

        if not query_variants:
            return None

        # Extract GT from matched variant(s)
        for variant in query_variants:
            # Match exact variant (REF and ALT must match)
            if variant.POS != pos:
                continue
            if variant.REF != ref:
                continue
            if alt not in variant.ALT:
                continue
            # Extract genotype for first sample (proband).
            #
            # NOTE: We deliberately do NOT use variant.gt_types here.
            # cyvcf2 has a confirmed, long-standing upstream bug (see
            # https://github.com/brentp/cyvcf2/issues/21, still present in
            # 0.31.1): for multiallelic sites, a sample homozygous for the
            # 2nd/3rd/... ALT allele is misclassified as UNKNOWN instead of
            # HOM_ALT. Empirically confirmed on a minimal synthetic VCF
            # (REF=A, ALT=T,G): GT=1/1 -> gt_types=UNKNOWN, GT=2/2 ->
            # gt_types=UNKNOWN, only GT=1/2 correctly gave HET. This
            # silently produced zygosity=None for any patient homozygous
            # for any allele at a multiallelic site.
            #
            # Fix: classify zygosity directly from the raw genotype allele
            # indices (variant.genotypes), confirmed correct in every
            # tested case including the ones gt_types got wrong. This also
            # correctly handles a sample not carrying the SPECIFIC alt
            # allele being queried (returns None instead of a false
            # "homozygous"), which gt_types alone cannot distinguish at
            # multiallelic sites.
            if len(variant.genotypes) == 0:
                return None

            genotype = variant.genotypes[0]  # [allele1, allele2, phased]
            if genotype is None or len(genotype) < 2:
                return None

            # variant.ALT is 0-based; VCF/GT allele numbering is 1-based
            # for ALTs (0 = REF, 1 = ALT[0], 2 = ALT[1], ...).
            try:
                allele_index = 1 + list(variant.ALT).index(alt)
            except ValueError:
                return None  # shouldn't happen given the ALT membership check above

            alleles = [a for a in genotype[:2] if isinstance(a, int)]
            n_copies = alleles.count(allele_index)

            if n_copies == 0:
                return None  # sample doesn't carry this specific allele
            elif n_copies == 1:
                chrom_upper = chrom.upper().replace("CHR", "")
                if chrom_upper in ("X", "23") and proband_sex == "Male":
                    return "hemizygous"
                return "heterozygous"
            else:  # n_copies == 2
                return "homozygous"

        # Variant not found in VCF
        return None

    except Exception as e:
        logger.warning(f"Failed to extract zygosity from VCF: {e}")
        return None


# ===========================================================================
# Gene-level reference loaders (called once, cached in module scope)
# ===========================================================================

_gnomad_constraint_cache: Optional[Dict] = None
_clingen_cache: Optional[Dict] = None
_hgnc_cache: Optional[Dict] = None
_clinvar_gene_lof_cache: Optional[Dict] = None

# VCF reader cache for zygosity extraction (avoid reopening for every variant)
_vcf_reader_cache: Dict[str, any] = {}  # {vcf_path: VCF_object}


def _load_gnomad_constraint() -> Dict[str, Dict]:
    """
    Load gnomAD v2.1.1 constraint metrics indexed by gene symbol.
    Returns: {gene: {pLI, oe_lof_upper (LOEUF), oe_mis_z}}
    """
    global _gnomad_constraint_cache
    if _gnomad_constraint_cache is not None:
        return _gnomad_constraint_cache

    path = Path(DATABASE_PATHS["gnomad_constraint"])
    cache: Dict[str, Dict] = {}

    if not path.exists():
        logger.warning(f"gnomAD constraint file not found: {path}")
        _gnomad_constraint_cache = cache
        return cache

    try:
        # Detect BGZF/gzip by magic bytes regardless of file extension
        with open(path, "rb") as _f:
            _magic = _f.read(2)
        opener = _gzip.open if _magic == b'\x1f\x8b' else open
        with opener(path, "rt", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                gene = row.get("gene", "").strip()
                if not gene:
                    continue
                try:
                    cache[gene] = {
                        "pLI":   float(row.get("pLI", "nan")),
                        "loeuf": float(row.get("oe_lof_upper", "nan")),
                        "z":     float(row.get("oe_mis_z", "nan")),
                    }
                except (ValueError, KeyError):
                    continue
        logger.info(f"Loaded gnomAD constraint for {len(cache)} genes.")
    except Exception as e:
        logger.warning(f"Could not load gnomAD constraint: {e}")

    _gnomad_constraint_cache = cache
    return cache


def _load_clingen() -> Dict[str, str]:
    """
    Load ClinGen gene-disease validity classifications indexed by gene symbol.
    Returns: {gene: classification}  e.g. {"BRCA2": "Definitive"}
    """
    global _clingen_cache
    if _clingen_cache is not None:
        return _clingen_cache

    path = Path(DATABASE_PATHS["clingen_validity"])
    cache: Dict[str, str] = {}

    if not path.exists():
        logger.warning(f"ClinGen validity file not found: {path}")
        _clingen_cache = cache
        return cache

    try:
        with open(path, "r", encoding="utf-8") as fh:
            # ClinGen CSV has variable header lines starting with #
            # Skip 4 metadata lines before the real header
            for _ in range(4):
                next(fh)
            reader = csv.DictReader(fh)
            for row in reader:
                gene = (row.get("GENE SYMBOL") or "").strip().strip('"')
                classification = (row.get("CLASSIFICATION") or "").strip().strip('"')    
                if gene and classification:
                    # Keep highest classification if gene appears multiple times
                    _RANK = {
                        "Definitive": 5, "Strong": 4, "Moderate": 3,
                        "Limited": 2, "Animal Model Only": 1,
                        "No Known Disease Relationship": 0,
                        "Disputed": 0, "Refuted": 0,
                    }
                    existing_rank = _RANK.get(cache.get(gene, ""), -1)
                    new_rank = _RANK.get(classification, -1)
                    if new_rank > existing_rank:
                        cache[gene] = classification
        logger.info(f"Loaded ClinGen validity for {len(cache)} genes.")
    except Exception as e:
        logger.warning(f"Could not load ClinGen validity: {e}")

    _clingen_cache = cache
    return cache


def _load_clinvar_gene_lof_summary(genes: set) -> Dict[str, Dict]:
    """
    Gene-level ClinVar LoF track-record summary, computed ONCE per session
    for only the genes present in this VCF — single-threaded, before the
    9-agent parallel fan-out. Same caching pattern as _load_gnomad_constraint /
    _load_clingen. Agent 4 does NOT compute this and is untouched by this
    addition; this reads the same underlying ClinVar Parquet source
    (src/rag/parquet_retriever.py) at gene-level instead of variant-level.

    For each gene, computes:
      lof_count:    # Pathogenic/Likely_pathogenic ClinVar records with a
                     LoF-type consequence
      lof_fraction: lof_count / total P/LP records for the gene
      multi_exon:   whether those LoF P/LP records span >1 distinct exon
                     (falls back to >1 distinct protein_pos if no exon
                     column is available — a coarser proxy, noted below)

    IMPORTANT — schema uncertainty: parquet_retriever.py's own query
    functions only ever SELECT {chrom,pos,ref,alt,gene,clnsig,stars,hgvs,
    protein_pos,variant_id}; whether a 'consequence' or 'exon' column
    exists in the underlying Parquet file is NOT confirmed. This function
    introspects the schema at runtime (DESCRIBE) and only uses those
    columns if they're actually present. If 'consequence' is absent,
    lof_count/lof_fraction/multi_exon are left as None for ALL genes
    (same as the pre-existing behavior — no regression, just no upgrade)
    and a one-time warning is logged so this is easy to notice and fix
    once the real schema is confirmed.
    """
    global _clinvar_gene_lof_cache
    if _clinvar_gene_lof_cache is not None:
        return _clinvar_gene_lof_cache

    cache: Dict[str, Dict] = {}
    if not genes:
        _clinvar_gene_lof_cache = cache
        return cache

    try:
        import duckdb
        from src.rag.parquet_retriever import PARQUET_DIR

        # Mirrors parquet_retriever.py's own path convention
        # (PARQUET_DIR / f"clinvar_{build_lower}"). Genome build isn't
        # threaded through post_process's gene-level loaders today (unlike
        # per-variant HGVS generation, which does read genome_build from
        # base_state) — defaulting to GRCh38 here to match this pipeline's
        # documented default; adjust if you routinely run GRCh37.
        genome_build = "grch38"
        parquet_path = PARQUET_DIR / f"clinvar_{genome_build}"

        if not parquet_path.exists():
            logger.warning(f"ClinVar Parquet not found for gene-level LoF summary: {parquet_path}")
            _clinvar_gene_lof_cache = cache
            return cache

        conn = duckdb.connect(":memory:")
        glob_path = f"{parquet_path}/**/*.parquet"

        # Introspect available columns rather than assuming
        available_cols = {
            row[0] for row in conn.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{glob_path}') LIMIT 0"
            ).fetchall()
        }
        has_consequence = "consequence" in available_cols
        has_exon = "exon" in available_cols

        if not has_consequence:
            logger.warning(
                "ClinVar Parquet has no 'consequence' column — cannot determine which "
                "P/LP records are LoF-type. gene_clinvar_lof_fraction/count/multi_exon "
                "will remain None for all genes (same as before this addition). "
                "Run `DESCRIBE SELECT * FROM read_parquet(...)` on the ClinVar Parquet "
                "to see actual available columns and adjust this function."
            )
            _clinvar_gene_lof_cache = cache
            return cache

        select_cols = "gene, clnsig, consequence"
        if has_exon:
            select_cols += ", exon"
        else:
            select_cols += ", protein_pos"  # coarser multi-exon proxy fallback

        gene_list_sql = "', '".join(g.replace("'", "''") for g in genes)

        query = f"""
            SELECT {select_cols}
            FROM read_parquet('{glob_path}')
            WHERE gene IN ('{gene_list_sql}')
              AND (clnsig ILIKE '%Pathogenic%' OR clnsig ILIKE '%Likely_pathogenic%'
                   OR clnsig ILIKE '%Likely pathogenic%')
              AND clnsig NOT ILIKE '%Conflicting%'
              AND clnsig NOT ILIKE '%Benign%'
        """
        rows = conn.execute(query).fetchall()
        col_names = [d[0] for d in conn.description]

        LOF_CONSEQUENCES_SET = {
            "stop_gained", "frameshift_variant",
            "splice_acceptor_variant", "splice_donor_variant",
        }

        by_gene: Dict[str, list] = {}
        for r in rows:
            rec = dict(zip(col_names, r))
            by_gene.setdefault(rec["gene"], []).append(rec)

        for gene, recs in by_gene.items():
            total_plp = len(recs)
            if total_plp == 0:
                continue
            lof_recs = [r for r in recs if (r.get("consequence") or "") in LOF_CONSEQUENCES_SET]
            lof_count = len(lof_recs)
            if has_exon:
                spread_key = "exon"
            else:
                spread_key = "protein_pos"
            distinct_positions = {r.get(spread_key) for r in lof_recs if r.get(spread_key) is not None}
            cache[gene] = {
                "lof_count": lof_count,
                "lof_fraction": lof_count / total_plp,
                "multi_exon": len(distinct_positions) > 1,
            }

        logger.info(
            f"Computed gene-level ClinVar LoF summary for {len(cache)} of {len(genes)} "
            f"genes in this VCF (exon column available: {has_exon})."
        )

    except Exception as e:
        logger.warning(f"Could not compute gene-level ClinVar LoF summary: {e}")

    _clinvar_gene_lof_cache = cache
    return cache


# ===========================================================================
# Value parsers — all return None on missing/invalid input
# ===========================================================================

def _float(value: str, transcript_id: str = None, transcript_list: str = None) -> Optional[float]:
    """
    Parse a VEP field to float. Returns None for '.', '', 'nan'.

    For multi-transcript dbNSFP values:
    - If transcript_id and transcript_list provided, match by transcript position
    - Otherwise, return first valid numeric value (fallback for non-transcript-specific fields)

    Args:
        value: The score value(s) from dbNSFP (may be comma-separated)
        transcript_id: Current row's Ensembl transcript ID (e.g., "ENST00000544455")
        transcript_list: Comma-separated list of transcript IDs from dbNSFP Ensembl_transcriptid field
    """
    if not value or value in (".", "-", "nan", "NA", "N/A"):
        return None

    # Single value case
    if "," not in value:
        try:
            return float(value)
        except ValueError:
            return None

    # Multi-value case: comma-separated scores for multiple transcripts
    parts = [p.strip() for p in value.split(",")]

    # Try transcript-specific matching if both IDs provided
    if transcript_id and transcript_list and "," in transcript_list:
        transcript_ids = [t.strip() for t in transcript_list.split(",")]
        # Match by full ID or by base ID without version (ENST00000544455.1 -> ENST00000544455)
        transcript_base = transcript_id.split(".")[0]

        for i, tid in enumerate(transcript_ids):
            tid_base = tid.split(".")[0]
            if tid == transcript_id or tid_base == transcript_base:
                if i < len(parts):
                    part = parts[i]
                    if part and part not in (".", "-", "nan", "NA", "N/A"):
                        try:
                            return float(part)
                        except ValueError:
                            pass
                break

    # Fallback: return first valid numeric value
    for part in parts:
        if part and part not in (".", "-", "nan", "NA", "N/A"):
            try:
                return float(part)
            except ValueError:
                continue

    return None


def _int(value: str) -> Optional[int]:
    if not value or value in (".", "-"):
        return None
    try:
        return int(value.split("-")[0])   # handle ranges like "123-456"
    except ValueError:
        return None


def _str(value: str) -> Optional[str]:
    # Handle both string and float/NaN values from pandas
    if value is None:
        return None
    if isinstance(value, float):
        if pd.isna(value):
            return None
        value = str(value)
    value = str(value).strip()
    if not value or value in (".", "-", "nan"):
        return None
    return value


def _parse_spliceai(value: str) -> float:
    """
    Parse SpliceAI_pred field.
    Format: GENE|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL
    Returns max delta score across all four splice site types.
    """
    if not value or value in (".", "-"):
        return 0.0
    max_score = 0.0
    for record in value.split(","):
        parts = record.split("|")
        if len(parts) >= 5:
            for i in (1, 2, 3, 4):   # DS_AG, DS_AL, DS_DG, DS_DL
                score = _float(parts[i])
                if score is not None and score > max_score:
                    max_score = score
    return max_score


def _parse_clinvar_stars(clnrevstat: str) -> int:
    """Convert CLNREVSTAT string to star count (0–4)."""
    if not clnrevstat or clnrevstat in (".", "-"):
        return 0
    key = clnrevstat.lower().replace(" ", "_")
    return _CLNREVSTAT_STARS.get(key, 0)


def _format_lof_status(lof_tag: Optional[str], lof_filter: Optional[str], lof_flags: Optional[str]) -> str:
    """
    Format human-readable LoF status for reports.

    Args:
        lof_tag: "HC" (high confidence) or "LC" (low confidence) or empty
        lof_filter: Reason for not being HC (e.g., "SINGLE_EXON", "END_TRUNC")
        lof_flags: Additional warnings (e.g., "PHYLOCSF_WEAK")

    Returns:
        Human-readable string for reports
    """
    if not lof_tag or lof_tag == "-":
        return "Not predicted LoF"

    if lof_tag == "HC":
        if lof_flags:
            # High confidence but with caveats
            flag_text = lof_flags.replace("_", " ").lower()
            return f"High confidence LoF (warning: {flag_text})"
        return "High confidence LoF"

    if lof_tag == "LC":
        if lof_filter:
            # Low confidence with reason
            filter_map = {
                "SINGLE_EXON": "single exon gene",
                "END_TRUNC": "truncation in last exon",
                "SMALL_INTRON": "small intron (<15bp)",
                "NON_CAN_SPLICE": "non-canonical splice site",
                "NON_CAN_SPLICE_SURR": "non-canonical surrounding splice sites",
                "EXON_INTRON_UNDEF": "exon/intron boundary undefined",
                "ANC_ALLELE": "ancestral allele discordance",
            }
            readable = filter_map.get(lof_filter, lof_filter.replace("_", " ").lower())
            return f"Low confidence LoF ({readable})"
        return "Low confidence LoF"

    # Fallback
    return f"LoF: {lof_tag}"


def _max_gnomad_af(row: Dict, pop_cols: Dict) -> Tuple[float, Dict[str, float]]:
    """
    Extract max gnomAD AF and per-population AF dict from a VEP row.
    Returns (max_af, {pop: af})
    """
    by_pop: Dict[str, float] = {}
    max_af = 0.0

    # gnomAD overall AF from VEP --custom gnomAD VCF
    global_af = _float(row.get("gnomAD_AF", ".") or ".")
    if global_af is not None and global_af > max_af:
        max_af = global_af

    for pop, col in pop_cols.items():
        af = _float(row.get(col, ".") or ".")
        if af is not None:
            by_pop[pop] = af
            if af > max_af:
                max_af = af

    return max_af, by_pop


def _insilico_votes(row: Dict, cfg_revel_path: float = 0.75,
                    cfg_revel_ben: float = 0.15,
                    cfg_cadd: int = 20) -> Tuple[int, int]:
    """
    Count how many in-silico tools call damaging vs benign.
    Returns (n_damaging, n_benign).
    """
    dam, ben = 0, 0

    # Get transcript ID for matching multi-transcript scores
    transcript_id = row.get("Feature", "")
    transcript_list = row.get("Ensembl_transcriptid", "")

    revel = _float(row.get("REVEL_score", ".") or ".", transcript_id, transcript_list)
    if revel is not None:
        if revel >= cfg_revel_path:
            dam += 1
        elif revel <= cfg_revel_ben:
            ben += 1

    cadd = _float(row.get("CADD_phred", ".") or ".", transcript_id, transcript_list)
    if cadd is not None:
        if cadd >= cfg_cadd:
            dam += 1
        else:
            ben += 1

    # PolyPhen: D/P = damaging, B = benign
    pp2 = _str(row.get("Polyphen2_HDIV_score", ".") or ".")
    # VEP --everything gives numeric score, not category
    pp2_score = _float(row.get("Polyphen2_HDIV_score", ".") or ".", transcript_id, transcript_list)
    if pp2_score is not None:
        if pp2_score >= 0.909:
            dam += 1
        elif pp2_score <= 0.446:
            ben += 1

    # SIFT: lower = more damaging (<0.05 = deleterious)
    sift = _float(row.get("SIFT_score", ".") or ".", transcript_id, transcript_list)
    if sift is not None:
        if sift < 0.05:
            dam += 1
        else:
            ben += 1

    return dam, ben


# ===========================================================================
# Main row parser
# ===========================================================================

def _parse_vep_row(
    row: Dict[str, str],
    session_id: str,
    base_state: VariantState,
    gnomad_constraint: Dict,
    clingen: Dict,
    clinvar_gene_lof: Dict,
) -> Optional[VariantState]:
    """
    Parse one VEP TSV row into a VariantState.
    Returns None if the row should be skipped (non-canonical, non-coding etc).
    """
    # DEBUG: Log first row's keys to verify column names
    global _DEBUG_LOGGED_COLUMNS
    if '_DEBUG_LOGGED_COLUMNS' not in globals():
        _DEBUG_LOGGED_COLUMNS = True
        logger.info(f"[{session_id}] DEBUG: Row keys (first 30): {list(row.keys())[:30]}")
        logger.info(f"[{session_id}] DEBUG: Sample values - CANONICAL='{row.get('CANONICAL')}', BIOTYPE='{row.get('BIOTYPE')}', Feature_type='{row.get('Feature_type')}'")

    # Helper function to safely get string value (handles both str and float/NaN)
    def _safe_str(value):
        """Convert value to string safely, handling NaN/float."""
        if value is None or value == "":
            return ""
        if isinstance(value, float):
            # pandas NaN or numeric value
            if pd.isna(value):
                return ""
            return str(value)
        return str(value)

    # Only process canonical transcript rows
    canonical = _safe_str(row.get("CANONICAL", "")).strip().upper()
    if canonical != "YES":
        return None

    # Skip non-protein-coding feature types (regulatory, motif features)
    feature_type = _safe_str(row.get("Feature_type", "")).strip()
    if feature_type not in ("Transcript", ""):
        return None

    # Skip non-protein-coding transcripts (lncRNA, miRNA, etc.)
    # BIOTYPE column added with --biotype flag (requires VEP re-run if missing)
    biotype = _safe_str(row.get("BIOTYPE", "")).strip().lower()
    if biotype and biotype not in ("protein_coding", ""):
        # Skip lncRNA, miRNA, snoRNA, etc.
        logger.debug(f"Filtered non-protein-coding transcript: {biotype}")
        return None

    # Parse variant ID
    uploaded = row.get("Uploaded_variation", "")
    location = row.get("Location", "")
    allele   = row.get("Allele", "")

    # Normalise to chr:pos:ref:alt and extract components for zygosity lookup
    chrom, pos_int, ref, alt = None, None, None, None
    variant_id = None  # Initialize to avoid UnboundLocalError

    # ALLELE_NUM (from --allele_number in vep_runner.py) is a 1-based index
    # into the ALT allele list embedded in Uploaded_variation. Using it here
    # instead of the VEP "Allele" column fixes the same problem already
    # fixed in append_annotations.py (handoff bug #5): "Allele" is VEP's
    # normalized form (e.g. "-" for a deletion) and does not reliably map
    # back to the literal VCF ALT for indels or multiallelic sites.
    #
    # ALLELE_NUM may be ABSENT if this TSV came from an externally-annotated
    # VCF that bypassed vep_runner_node entirely (see graph.py's
    # _should_run_vep / vep_already_annotated) - such input was never run
    # with --allele_number. We do NOT hard-fail on that; we fall back to the
    # pre-existing Allele-based behavior, unchanged, so that path keeps
    # working exactly as it did before this patch.
    allele_num_raw = row.get("ALLELE_NUM", "")
    try:
        allele_num = int(allele_num_raw)
    except (ValueError, TypeError):
        allele_num = None

    # Try parsing Uploaded_variation first (format: chr1_12345_A/G)
    if "_" in uploaded and "/" in uploaded:
        parts = uploaded.split("_")
        if len(parts) >= 3:
            # Handle alternate contigs like NT_187361.1_40583_A/G
            # where parts = ["NT", "187361.1", "40583", "A/G"]
            # The position is the LAST integer before the slash
            try:
                # Try parsing parts[1] as position (standard chr format: chr1_12345_A/G)
                if "." not in parts[1]:
                    chrom = parts[0]
                    pos_int = int(parts[1])
                    alleles = parts[2].split("/")
                else:
                    # Alternate contig format: NT_187361.1_40583_A/G
                    # Chromosome is parts[0]_parts[1], position is parts[2]
                    chrom = f"{parts[0]}_{parts[1]}"
                    pos_int = int(parts[2])
                    alleles = parts[3].split("/") if len(parts) > 3 else []

                ref = alleles[0] if alleles else "."

                if allele_num is not None and 0 < allele_num < len(alleles):
                    # Correct: literal ALT indexed straight out of
                    # Uploaded_variation, same mechanism as append_annotations.py
                    alt = alleles[allele_num]
                else:
                    # Fallback: unchanged prior behavior
                    alt = allele

                variant_id = f"{chrom}:{pos_int}:{ref}:{alt}"
            except (ValueError, IndexError) as e:
                logger.debug(f"[{session_id}] Could not parse uploaded_variation '{uploaded}': {e}")
                variant_id = uploaded
        else:
            variant_id = uploaded

    # Fallback: parse from Location (format: "1:12345-12345" or "chr1:12345")
    # VEP doesn't provide REF in TSV by default, so we'll need to look it up from VCF
    if not chrom or not pos_int:
        try:
            if ":" in location:
                loc_parts = location.split(":")
                if len(loc_parts) == 2:
                    chrom = loc_parts[0]
                    # Handle both "12345-12345" and "12345" formats
                    pos_str = loc_parts[1].split("-")[0]
                    pos_int = int(pos_str)
                    alt = allele

                    # Try to get REF from the VCF using cyvcf2
                    if CYVCF2_AVAILABLE:
                        vcf_path = base_state.get("filtered_vcf") or base_state.get("proband_vcf_path")
                        if vcf_path and Path(vcf_path).exists():
                            try:
                                vcf_reader = VCF(str(vcf_path))  # VCF is already imported at top
                                # Try to find this variant in VCF to get REF
                                for variant in vcf_reader(f"{chrom}:{pos_int}-{pos_int}"):
                                    if variant.POS == pos_int:
                                        ref = variant.REF
                                        break
                            except Exception as e:
                                logger.debug(f"[{session_id}] Could not look up REF from VCF: {e}")
                                ref = None

                    if not ref:
                        # Cannot determine ref - will skip zygosity extraction
                        ref = None

                    if not variant_id or variant_id == uploaded:
                        variant_id = f"{chrom}:{pos_int}:{ref or '?'}:{alt}"
        except (ValueError, IndexError) as e:
            logger.warning(f"[{session_id}] Could not parse location '{location}': {e}")
            variant_id = uploaded or f"{location}:{allele}"

    # Final fallback: ensure variant_id is never None
    if not variant_id:
        variant_id = uploaded or f"{location}:{allele}"
        logger.warning(f"[{session_id}] Using fallback variant_id: {variant_id}")

    gene       = _str(row.get("SYMBOL", "")) or ""
    transcript = _str(row.get("Feature", ""))
    consequence = row.get("Consequence", "").split(",")[0].strip()

    # Population frequency
    max_af, af_by_pop = _max_gnomad_af(row, _GNOMAD_POP_COLS)
    gnomad_popmax = max(af_by_pop.values()) if af_by_pop else 0.0
    gnomad_nhomalt = int(_float(row.get("gnomAD_nhomalt", "0") or "0") or 0)

    # ClinVar
    clinvar_sig = _str(row.get("ClinVar_CLNSIG", "") or "")
    clinvar_stars = _parse_clinvar_stars(row.get("ClinVar_CLNREVSTAT", "") or "")
    clinvar_disease = _str(row.get("ClinVar_CLNDN", "") or "")
    clinvar_acc = _str(row.get("ClinVar_CLNACC", "") or "")

    # In-silico scores (with transcript matching for multi-transcript dbNSFP values)
    transcript_id = row.get("Feature", "")
    transcript_list = row.get("Ensembl_transcriptid", "")

    spliceai    = _parse_spliceai(row.get("SpliceAI_pred", "") or "")
    revel       = _float(row.get("REVEL_score", "") or "", transcript_id, transcript_list)
    cadd        = _float(row.get("CADD_phred", "") or "", transcript_id, transcript_list)
    sift        = _float(row.get("SIFT_score", "") or "", transcript_id, transcript_list)
    pp2         = _float(row.get("Polyphen2_HDIV_score", "") or "", transcript_id, transcript_list)
    phylop      = _float(row.get("phyloP100way_vertebrate", "") or "", transcript_id, transcript_list)
    gerp        = _float(row.get("GERP++_RS", "") or "", transcript_id, transcript_list)
    metasvm     = _float(row.get("MetaSVM_score", "") or "", transcript_id, transcript_list)

    # LOFTEE
    lof_tag     = _str(row.get("LoF", "") or "")
    lof_filter  = _str(row.get("LoF_filter", "") or "")
    lof_flags   = _str(row.get("LoF_flags", "") or "")
    lof_info    = _str(row.get("LoF_info", "") or "")  # raw KEY:VALUE,... string —
                                                          # used by Agent 2 for de-novo
                                                          # splice-rescue-probability check
    is_loftee_hc = lof_tag == "HC"

    # Format human-readable LoF status
    lof_status = _format_lof_status(lof_tag, lof_filter, lof_flags)

    # In-silico votes
    dam_votes, ben_votes = _insilico_votes(row)

    # Structural flags
    csq_set = set(consequence.split("&"))
    is_inframe = bool(csq_set & _INFRAME_CONSEQUENCES)
    is_stop_loss = "stop_lost" in csq_set
# repeat_region: True if VEP FLAGS column contains low_complexity marker
    # RepeatMasker-based richer check is deferred to agent 8 (PM4/BP3)
    vep_flags = (row.get("FLAGS", "") or "").lower()
    is_repeat_region = "low_complexity" in vep_flags or "repeat" in vep_flags
    # Protein position
    prot_pos = _int(row.get("Protein_position", "") or "")

    # Exon / intron numbers
    exon_num   = _str(row.get("EXON", "") or "")
    intron_num = _str(row.get("INTRON", "") or "")

    # HGVSc / HGVSp
    hgvsc = _str(row.get("HGVSc", "") or "")
    hgvsp = _str(row.get("HGVSp", "") or "")

    # Fallback: generate genomic HGVS when HGVSc is blank (e.g., for intronic variants)
    if not hgvsc and chrom and pos_int and ref and alt:
        hgvsc = _generate_genomic_hgvs(chrom, pos_int, ref, alt, base_state.get("genome_build", "GRCh38"))

    # Gene-level context from reference databases
    constraint = gnomad_constraint.get(gene, {})
    pli   = constraint.get("pLI")
    loeuf = constraint.get("loeuf")
    z     = constraint.get("z")
    clingen_val = clingen.get(gene)
    gene_lof_summary = clinvar_gene_lof.get(gene, {})

    # Extract zygosity from VCF GT field
    zygosity = None
    logger.debug(f"[{session_id}] Pre-zygosity check: chrom={chrom}, pos_int={pos_int}, ref={ref}, alt={alt}")
    # Only extract zygosity if we have valid ref allele (not placeholder "-")
    if chrom and pos_int and ref and alt and ref != "-":
        # ONLY extract zygosity for standard chromosomes (chr1-22, X, Y, MT)
        # Skip alternate contigs (NT_*, NW_*, etc.) to avoid VCF iteration bottleneck
        chrom_clean = chrom.replace("chr", "").upper()
        standard_chroms = [str(i) for i in range(1, 23)] + ["X", "Y", "MT", "M"]

        logger.debug(f"[{session_id}] Zygosity check: chrom={chrom}, chrom_clean={chrom_clean}, is_standard={chrom_clean in standard_chroms}")

        if chrom_clean in standard_chroms:
            # Prefer filtered VCF (post-prefilter), fallback to original
            vcf_path = base_state.get("filtered_vcf") or base_state.get("proband_vcf_path")
            proband_sex = base_state.get("proband_sex", "Unknown")
            logger.debug(f"[{session_id}] Attempting zygosity extraction: vcf={vcf_path}, chr={chrom}:{pos_int}")
            if vcf_path and Path(vcf_path).exists():
                zygosity = _extract_zygosity_from_vcf(
                    vcf_path, chrom, pos_int, ref, alt, proband_sex
                )
                logger.debug(f"[{session_id}] Zygosity result: {zygosity}")
            else:
                logger.warning(f"[{session_id}] VCF not found for zygosity extraction: {vcf_path}")
        else:
            logger.debug(f"[{session_id}] Skipping zygosity extraction for alternate contig: {chrom}")

    # Extract parental genotypes if trio mode
    parent1_genotype = None
    parent2_genotype = None

    if base_state.get("trio_mode") and chrom and pos_int and ref and alt:
        parent1_vcf = base_state.get("parent1_vcf_path")
        parent2_vcf = base_state.get("parent2_vcf_path")

        if parent1_vcf:
            parent1_genotype = _extract_parental_genotype(
                parent1_vcf, chrom, pos_int, ref, alt
            )
            if parent1_genotype:
                logger.debug(f"[{session_id}] {variant_id} - Parent1 GT: {parent1_genotype}")

        if parent2_vcf:
            parent2_genotype = _extract_parental_genotype(
                parent2_vcf, chrom, pos_int, ref, alt
            )
            if parent2_genotype:
                logger.debug(f"[{session_id}] {variant_id} - Parent2 GT: {parent2_genotype}")

        # Log trio status
        if parent1_genotype and parent2_genotype:
            logger.info(
                f"[{session_id}] {variant_id} - Trio genotypes: "
                f"Proband={zygosity or '?'}, Parent1={parent1_genotype}, Parent2={parent2_genotype}"
            )

    # Build state — start from base and overlay parsed fields
    state = dict(base_state)   # shallow copy of base
    state.update({
        # Phase 1 — variant identity
        "variant_id":       variant_id,
        "gene":             gene,
        "transcript":       transcript,
        "hgvsc":            hgvsc,
        "hgvsp":            hgvsp,
        "consequence":      consequence,
        "protein_position": prot_pos,
        "exon_number":      exon_num,
        "intron_number":    intron_num,
        "zygosity":         zygosity,  # het/hom/hemi from VCF GT field

        # Trio mode - parental genotypes (extracted from parent VCFs)
        "parent1_genotype": parent1_genotype,  # e.g., "0/0", "0/1", "1/1"
        "parent2_genotype": parent2_genotype,  # e.g., "0/0", "0/1", "1/1"

        # Phase 2 — population frequency
        "max_gnomad_af":           max_af,
        "gnomad_af_popmax":        gnomad_popmax,
        "gnomad_nhomalt":          gnomad_nhomalt,
        "gnomad_af_by_population": af_by_pop,

        # Phase 3 — ClinVar
        "clinvar_classification": clinvar_sig,
        "clinvar_review_stars":   clinvar_stars,
        "clinvar_disease":        clinvar_disease,
        "clinvar_accession":      clinvar_acc,

        # Phase 4 — in-silico scores
        "is_loftee_hc":           is_loftee_hc,
        "lof_filter":             lof_filter,
        "lof_flags":              lof_flags,
        "lof_info":               lof_info,
        "lof_status":             lof_status,
        "max_spliceai":           spliceai,
        "revel_score":            revel,
        "cadd_phred":             cadd,
        "sift_score":             sift,
        "polyphen2_score":        pp2,
        "metasvm_score":          metasvm,
        "mutationtaster_score":   None,   # not in dbNSFP 5.3.1a by default
        "eve_score":              None,   # not requested in VEP plugin flags
        "maxentscan_diff":        None,   # requires separate MaxEntScan plugin run
        "gerp_rs":                gerp,
        "phylop100way":           phylop,
        "insilico_votes_damaging": dam_votes,
        "insilico_votes_benign":   ben_votes,

        # Phase 5 — structural flags
        "is_inframe_indel": is_inframe,
        "is_stop_loss":     is_stop_loss,
        "repeat_region": is_repeat_region,
        # Phase 6 — gene-level context
        "gene_clingen_validity":    clingen_val,
        "gene_gnomad_pli":          pli,
        "gene_gnomad_loeuf":        loeuf,
        "gene_gnomad_zscore":       z,
        # Gene-level ClinVar LoF track-record summary — computed ONCE per gene,
        # single-threaded, in this node (see _load_clinvar_gene_lof_summary below),
        # NOT by an agent at runtime. Fixes a pre-existing gap where these were
        # hardcoded None and never actually populated by anything.
        "gene_clinvar_missense_fraction": None,  # not computed yet — separate from LoF summary
        "gene_clinvar_lof_fraction":      gene_lof_summary.get("lof_fraction"),
        "gene_clinvar_lof_count":         gene_lof_summary.get("lof_count"),
        "gene_clinvar_lof_multi_exon":    gene_lof_summary.get("multi_exon"),
        # Orphanet inheritance — done by Agent 9 (needs Orphanet XML)
        "gene_orphanet_inheritance": None,
    })

    return state   # type: ignore[return-value]


# ===========================================================================
# Node entry point
# ===========================================================================

def post_process_node(state: VariantState) -> dict:
    """
    Parse the VEP TSV output and return a list of per-variant states.

    In the current graph design this node processes all variants in the TSV
    and stores them as a list under the key "parsed_variants" in state.
    The graph's fan-out logic (to be added in graph.py) then creates one
    VariantState per entry and dispatches agents.

    For now (stub graph), it returns the first variant's fields merged into
    state so the graph can continue as a single-variant pass.
    """
    session_id   = state["session_id"]
    warnings     = list(state.get("warnings", []))
    
    # Pass-2 invocation: variant fields already seeded by runner from pass-1
    # parsed_variants. Skip re-parsing to avoid overwriting with wrong variant.
    if state.get("variant_id") and state.get("gene") and state.get("vep_already_annotated"):
        logger.info(f"[{session_id}] post_process: variant fields already populated "
                    f"({state['variant_id']}) — skipping TSV re-parse.")
        return {"warnings": warnings}

    annotated_tsv = state.get("annotated_tsv")

    if not annotated_tsv or not Path(annotated_tsv).exists():
        warnings.append("POST_PROCESS_WARN: No annotated TSV found — skipping parse.")
        return {"warnings": warnings}

    tsv_path = Path(annotated_tsv)
    logger.info(f"[{session_id}] Parsing VEP output: {tsv_path.name}")

    # Load gene-level reference data (cached after first call)
    gnomad_constraint = _load_gnomad_constraint()
    clingen           = _load_clingen()
    # gene-level ClinVar LoF summary is loaded below, once we know which
    # genes are actually in this VCF (see after the pandas pre-filter step)

    # ------------------------------------------------------------------
    # Parse VEP TSV — skip comment lines starting with ##
    # The column header line starts with a single #
    # ------------------------------------------------------------------
    parsed_variants: List[VariantState] = []
    seen_variant_ids = set()

    try:
        with open(tsv_path, "r", encoding="utf-8") as fh:
            # Find the header line (starts with #Uploaded_variation)
            header_line = None
            for line in fh:
                if line.startswith("#Uploaded_variation"):
                    header_line = line.lstrip("#").rstrip("\n")
                    break
                # Skip ## comment lines

            if header_line is None:
                warnings.append("POST_PROCESS_WARN: Could not find VEP TSV header line.")
                return {"warnings": warnings}

            # Re-open from top — simpler than tracking position
        # OPTIMIZED: Use pandas for bulk TSV read (10-20× faster than csv.DictReader)
        PANDAS_AVAILABLE_LOCAL = True  # Initialize before use

        if PANDAS_AVAILABLE:
            logger.debug(f"[{session_id}] Using pandas for fast TSV parsing")
            try:
                # Read entire TSV in one shot (fast!)
                # VEP TSV has ## comment lines followed by #Uploaded_variation header
                # CRITICAL FIX: comment='#' skips BOTH ## and #header, making pandas use first data row as header!
                # We need to manually skip ## lines but keep the #header line
                with open(tsv_path, 'r', encoding='utf-8') as f:
                    lines = []
                    for line in f:
                        if line.startswith('##'):
                            continue  # Skip VEP metadata comments
                        elif line.startswith('#'):
                            # Header line - strip leading # and add it
                            lines.append(line[1:])  # Remove leading #
                        else:
                            lines.append(line)

                # Now parse with pandas from the filtered lines
                from io import StringIO
                df = pd.read_csv(
                    StringIO(''.join(lines)),
                    sep='\t',
                    dtype=str,     # Read all as strings to avoid type inference overhead
                    keep_default_na=False,     # Don't convert "NA" gene names to NaN
                    na_filter=False,           # Don't convert any values to NaN - read everything as-is
                    low_memory=False,
                    engine='c',    # Use fast C parser
                    header=0       # First line is now the clean header (without leading #)
                )

                # Column names are already clean (pandas removes leading #)
                # DEBUG: Log what pandas actually read
                logger.info(f"[{session_id}] DEBUG: Pandas read {len(df.columns)} columns")
                logger.info(f"[{session_id}] DEBUG: First 30 column names: {list(df.columns[:30])}")
                logger.info(f"[{session_id}] DEBUG: Key columns - CANONICAL={df.columns.tolist().count('CANONICAL')}, BIOTYPE={df.columns.tolist().count('BIOTYPE')}, Feature_type={df.columns.tolist().count('Feature_type')}")

                # DEBUG: Check first row
                if len(df) > 0:
                    first_row = df.iloc[0]
                    logger.info(f"[{session_id}] DEBUG: First row sample - SYMBOL='{first_row.get('SYMBOL') if 'SYMBOL' in df.columns else 'COL_MISSING'}', Consequence='{first_row.get('Consequence') if 'Consequence' in df.columns else 'COL_MISSING'}'")
                    logger.info(f"[{session_id}] DEBUG: First row filters - CANONICAL='{first_row.get('CANONICAL') if 'CANONICAL' in df.columns else 'COL_MISSING'}', BIOTYPE='{first_row.get('BIOTYPE') if 'BIOTYPE' in df.columns else 'COL_MISSING'}', Feature_type='{first_row.get('Feature_type') if 'Feature_type' in df.columns else 'COL_MISSING'}'")

                # CRITICAL: Convert ALL columns to string and strip whitespace
                # Despite dtype=str, pandas may still create float NaN for some values
                # We must explicitly convert everything to string to avoid AttributeError
                for col in df.columns:
                    df[col] = df[col].astype(str).str.strip()

                # Convert to list of dicts for _parse_vep_row compatibility
                rows = df.to_dict('records')

                logger.info(f"[{session_id}] Pandas loaded {len(rows)} rows from VEP TSV in bulk")

            except Exception as e:
                logger.warning(f"[{session_id}] Pandas parsing failed ({e}), falling back to csv.DictReader")
                PANDAS_AVAILABLE_LOCAL = False
        else:
            PANDAS_AVAILABLE_LOCAL = False

        # Fallback to csv.DictReader if pandas not available or failed
        if not PANDAS_AVAILABLE or not PANDAS_AVAILABLE_LOCAL:
            logger.debug(f"[{session_id}] Using csv.DictReader for TSV parsing (slower)")
            with open(tsv_path, "r", encoding="utf-8") as fh:
                # Skip to and read the data using csv.DictReader
                lines = [
                    line for line in fh
                    if not line.startswith("##")
                ]
                # Strip leading # from header
                if lines and lines[0].startswith("#"):
                    lines[0] = lines[0].lstrip("#")

                reader = csv.DictReader(lines, delimiter="\t")
                rows = list(reader)

            # csv.DictReader path: compute gene set from all rows (no pre-filter
            # step available here) before parsing.
            genes_in_vcf = {r.get("SYMBOL", "").strip() for r in rows} - {"", "nan", "NA"}
            clinvar_gene_lof = _load_clinvar_gene_lof_summary(genes_in_vcf)

        # OPTIMIZATION: If using pandas, apply filters BEFORE parsing to reduce rows
        # This reduces 2.8M rows → ~80k rows BEFORE expensive _parse_vep_row() calls
        # Speedup: 28 min → 3 min (10× faster!)
        if PANDAS_AVAILABLE and PANDAS_AVAILABLE_LOCAL:
            logger.info(f"[{session_id}] Applying pandas pre-filters to reduce parsing workload...")
            original_row_count = len(df)

            # Filter 1: CANONICAL only (removes ~50% of rows instantly)
            df = df[df['CANONICAL'].str.upper() == 'YES']
            logger.info(f"[{session_id}]   CANONICAL filter: {original_row_count} → {len(df)} rows")

            # Filter 2: Feature_type == Transcript (removes regulatory features)
            if 'Feature_type' in df.columns:
                df = df[df['Feature_type'] == 'Transcript']
                logger.info(f"[{session_id}]   Feature_type filter: → {len(df)} rows")

            # Filter 3: BIOTYPE == protein_coding (removes lncRNA, miRNA, pseudogenes)
            if 'BIOTYPE' in df.columns:
                df = df[df['BIOTYPE'].str.lower() == 'protein_coding']
                logger.info(f"[{session_id}]   BIOTYPE filter: → {len(df)} rows")

            # Filter 4: Exclude non-coding consequences (upstream, downstream, intergenic, intron)
            EXCLUDED_CONSEQUENCES = {
                "upstream_gene_variant",
                "downstream_gene_variant",
                "intergenic_variant",
                "intron_variant",
                "non_coding_transcript_exon_variant",
                "non_coding_transcript_variant",
            }
            if 'Consequence' in df.columns:
                # Extract first consequence (VEP uses comma-separated for multiple)
                df['_first_consequence'] = df['Consequence'].str.split(',').str[0].str.strip()
                df = df[~df['_first_consequence'].isin(EXCLUDED_CONSEQUENCES)]
                df = df.drop(columns=['_first_consequence'])
                logger.info(f"[{session_id}]   Consequence filter: → {len(df)} rows")

            # Filter 5: MAF threshold (remove common variants with pandas - MUCH faster)
            from src.config import PipelineConfig
            cfg = PipelineConfig()
            if cfg.maf_threshold > 0 and 'gnomAD_AF' in df.columns:
                # Convert gnomAD_AF to numeric (handles '.', 'nan', etc.)
                df['_gnomad_af_numeric'] = pd.to_numeric(df['gnomAD_AF'], errors='coerce').fillna(0)
                before_maf = len(df)
                df = df[df['_gnomad_af_numeric'] <= cfg.maf_threshold]
                df = df.drop(columns=['_gnomad_af_numeric'])
                logger.info(f"[{session_id}]   MAF filter (>{cfg.maf_threshold*100}%): {before_maf} → {len(df)} rows")

            # Convert back to list of dicts for _parse_vep_row
            rows = df.to_dict('records')
            logger.info(f"[{session_id}] Pre-filtering complete: {original_row_count} → {len(rows)} rows to parse")
            logger.info(f"[{session_id}] Parsing {len(rows)} filtered rows (this will take ~{len(rows)//1000} min)...")

            # Gene-level ClinVar LoF summary — computed once for only the genes
            # actually present in this (already-filtered) VCF, single-threaded,
            # before any agent runs. See _load_clinvar_gene_lof_summary().
            if 'SYMBOL' in df.columns:
                genes_in_vcf = set(df['SYMBOL'].dropna().unique()) - {'', 'nan', 'NA'}
            else:
                genes_in_vcf = set()
            clinvar_gene_lof = _load_clinvar_gene_lof_summary(genes_in_vcf)
        # else: clinvar_gene_lof was already set in the csv.DictReader fallback
        # branch above (same condition, De Morgan's law) — do not overwrite it here.

        # Process all rows (same logic for both pandas and csv.DictReader)
        row_count = 0
        filtered_counts = {
            "parse_returned_none": 0,
            "duplicate": 0,
            "excluded_consequence": 0,
            "common_maf": 0,
            "synonymous": 0,
        }

        for row in rows:
            row_count += 1
            # Strip whitespace from all values (pandas already did this, but csv needs it)
            if not PANDAS_AVAILABLE:
                row = {k.strip(): v.strip() for k, v in row.items() if k}

            variant_state = _parse_vep_row(
                row, session_id, state, gnomad_constraint, clingen, clinvar_gene_lof
            )
            if variant_state is None:
                filtered_counts["parse_returned_none"] += 1
                continue

            vid = variant_state.get("variant_id", "")
            if vid in seen_variant_ids:
                filtered_counts["duplicate"] += 1
                continue    # deduplicate — one canonical row per variant

            # Filter out non-coding consequence types with no clinical significance
            consequence = variant_state.get("consequence", "")
            EXCLUDED_CONSEQUENCES = {
                "upstream_gene_variant",
                "downstream_gene_variant",
                "intergenic_variant",
                "intron_variant",
                "non_coding_transcript_exon_variant",  # lncRNA, miRNA, etc. - not protein-coding
                "non_coding_transcript_variant",
            }

            if consequence in EXCLUDED_CONSEQUENCES:
                filtered_counts["excluded_consequence"] += 1
                logger.debug(f"[{session_id}] Filtered out {vid}: {consequence}")
                continue

            # MAF filter: Remove common variants (post-VEP, using gnomAD population frequency)
            # Only apply if maf_threshold > 0 (0 = disabled)
            from src.config import PipelineConfig
            cfg = PipelineConfig()
            if cfg.maf_threshold > 0:
                max_gnomad_af = variant_state.get("max_gnomad_af", 0.0) or 0.0
                if max_gnomad_af > cfg.maf_threshold:
                    filtered_counts["common_maf"] += 1
                    logger.debug(
                        f"[{session_id}] Filtered out {vid}: MAF={max_gnomad_af:.4f} "
                        f"> threshold {cfg.maf_threshold}"
                    )
                    continue

            # Special handling for synonymous variants: only keep if likely to affect splicing
            # Keep if SpliceAI ≥ 0.2 OR within 3bp of exon boundary
            if consequence == "synonymous_variant":
                spliceai = variant_state.get("max_spliceai", 0.0) or 0.0
                keep_variant = False

                # Criterion 1: SpliceAI ≥ 0.2 (likely splice-altering)
                if spliceai >= 0.2:
                    logger.debug(f"[{session_id}] Retained synonymous {vid}: SpliceAI={spliceai:.3f}")
                    keep_variant = True
                else:
                    # Criterion 2: Check if near exon-intron boundary
                    # VEP DISTANCE field indicates distance to nearest feature
                    # For synonymous variants AT exon boundaries, DISTANCE is usually 0 or near 0
                    distance = row.get("DISTANCE", "")
                    if distance and distance != "-":
                        try:
                            dist_val = int(distance)
                            if dist_val <= 3:  # Within 3bp of boundary
                                logger.debug(f"[{session_id}] Retained synonymous {vid}: DISTANCE={dist_val}bp from boundary")
                                keep_variant = True
                        except ValueError:
                            pass

                if not keep_variant:
                    filtered_counts["synonymous"] += 1
                    logger.debug(f"[{session_id}] Filtered synonymous {vid}: SpliceAI={spliceai:.3f} < 0.2, not at boundary")
                    continue

            seen_variant_ids.add(vid)
            parsed_variants.append(variant_state)

        # Log filtering breakdown
        logger.info(f"[{session_id}] Filtering breakdown: processed {row_count} rows")
        for reason, count in filtered_counts.items():
            logger.info(f"[{session_id}]   {reason}: {count}")

    except Exception as e:
        warnings.append(f"POST_PROCESS_ERROR: Failed to parse VEP TSV: {e}")
        logger.error(f"[{session_id}] VEP TSV parse error: {e}", exc_info=True)
        return {"warnings": warnings}

    # Log filtering summary
    from src.config import PipelineConfig
    cfg = PipelineConfig()

    logger.info(
        f"[{session_id}] Post-VEP filtering complete: {len(parsed_variants)} variants retained "
        f"from {tsv_path.name}"
    )
    logger.info(f"[{session_id}] DEBUG: parsed_variants type={type(parsed_variants)}, len={len(parsed_variants)}")
    if len(parsed_variants) > 0:
        logger.info(f"[{session_id}] DEBUG: First variant keys={list(parsed_variants[0].keys())[:10]}")

    if cfg.maf_threshold > 0:
        logger.info(
            f"[{session_id}] MAF filtering applied (threshold: {cfg.maf_threshold*100}%) — "
            f"common variants (gnomAD AF > {cfg.maf_threshold}) were excluded"
        )
    else:
        logger.info(f"[{session_id}] MAF filtering disabled (threshold=0) — all variants processed")

    if not parsed_variants:
        warnings.append(
            "POST_PROCESS_WARN: No canonical variants parsed from VEP output. "
            "Check that --canonical flag was used and VCF has protein-coding variants."
        )
        return {"warnings": warnings}

    # ------------------------------------------------------------------
    # For the current single-variant graph: merge first variant into state.
    # When fan-out is implemented, return {"parsed_variants": parsed_variants}
    # and the graph dispatcher creates one VariantState per variant.
    # ------------------------------------------------------------------
    first = parsed_variants[0]
    update = {k: v for k, v in first.items()
            if k not in ("session_id", "proband_vcf_path", "genome_build")}
    update["warnings"] = warnings
    update["parsed_variants_count"] = len(parsed_variants)
    update["parsed_variants"] = parsed_variants
    return update
