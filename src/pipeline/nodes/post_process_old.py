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
    from cyvcf2 import VCF
    CYVCF2_AVAILABLE = True
except ImportError:
    CYVCF2_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# gnomAD population AF columns in VEP TSV output
# ---------------------------------------------------------------------------
_GNOMAD_POP_COLS = {
    "afr": "gnomADe_AFR_AF",
    "amr": "gnomADe_AMR_AF",
    "asj": "gnomADe_ASJ_AF",
    "eas": "gnomADe_EAS_AF",
    "fin": "gnomADe_FIN_AF",
    "mid": "gnomADe_MID_AF",
    "nfe": "gnomADe_NFE_AF",
    "sas": "gnomADe_SAS_AF",
    "remaining": "gnomADe_REMAINING_AF",
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
    if not CYVCF2_AVAILABLE:
        logger.warning("cyvcf2 not available — cannot extract zygosity from VCF")
        return None

    try:
        vcf = VCF(vcf_path)

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

            # Extract GT for first sample (proband)
            if len(variant.gt_types) == 0:
                return None

            gt_type = variant.gt_types[0]

            # cyvcf2 gt_types encoding:
            # 0 = HOM_REF (0/0)
            # 1 = HET (0/1, 1/0)
            # 2 = HOM_ALT (1/1)
            # 3 = UNKNOWN (./.)

            if gt_type == 0:
                return None  # Homozygous reference — not a variant
            elif gt_type == 1:
                # Heterozygous — but check for X-chromosome males (hemizygous)
                chrom_upper = chrom.upper().replace("CHR", "")
                if chrom_upper in ("X", "23") and proband_sex == "Male":
                    return "hemizygous"
                return "heterozygous"
            elif gt_type == 2:
                return "homozygous"
            elif gt_type == 3:
                return None  # Unknown/no-call

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
    if not value or value in (".", "-", ""):
        return None
    return value.strip()


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


def _max_gnomad_af(row: Dict, pop_cols: Dict) -> Tuple[float, Dict[str, float]]:
    """
    Extract max gnomAD AF and per-population AF dict from a VEP row.
    Returns (max_af, {pop: af})
    """
    by_pop: Dict[str, float] = {}
    max_af = 0.0

    # gnomADe (exome) AFs from VEP --everything
    global_af = _float(row.get("gnomADe_AF", ".") or ".")
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
) -> Optional[VariantState]:
    """
    Parse one VEP TSV row into a VariantState.
    Returns None if the row should be skipped (non-canonical, non-coding etc).
    """
    # Only process canonical transcript rows
    if row.get("CANONICAL", "").strip().upper() != "YES":
        return None

    # Skip non-protein-coding feature types (regulatory, motif features)
    if row.get("Feature_type", "").strip() not in ("Transcript", ""):
        return None

    # Parse variant ID
    uploaded = row.get("Uploaded_variation", "")
    location = row.get("Location", "")
    allele   = row.get("Allele", "")

    # Normalise to chr:pos:ref:alt and extract components for zygosity lookup
    chrom, pos_int, ref, alt = None, None, None, None

    if "_" in uploaded and "/" in uploaded:
        parts = uploaded.split("_")
        if len(parts) >= 3:
            chrom = parts[0]
            pos_int = int(parts[1])
            ref_alt = parts[2].split("/")
            ref = ref_alt[0] if ref_alt else "."
            alt = allele
            variant_id = f"{chrom}:{pos_int}:{ref}:{alt}"
        else:
            variant_id = uploaded
    else:
        # Fallback: parse from Location (format: "1:12345-12345")
        variant_id = f"{location}:{allele}"
        if ":" in location and "-" in location:
            loc_parts = location.split(":")
            if len(loc_parts) == 2:
                chrom = loc_parts[0]
                pos_range = loc_parts[1].split("-")
                pos_int = int(pos_range[0])
                ref = row.get("REF_ALLELE", "")
                alt = allele

    gene       = _str(row.get("SYMBOL", "")) or ""
    transcript = _str(row.get("Feature", ""))
    consequence = row.get("Consequence", "").split(",")[0].strip()

    # Population frequency
    max_af, af_by_pop = _max_gnomad_af(row, _GNOMAD_POP_COLS)
    gnomad_popmax = max(af_by_pop.values()) if af_by_pop else 0.0

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
    is_loftee_hc = lof_tag == "HC"

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

    # Extract zygosity from VCF GT field
    zygosity = None
    if chrom and pos_int and ref and alt:
        # Prefer filtered VCF (post-prefilter), fallback to original
        vcf_path = base_state.get("filtered_vcf") or base_state.get("proband_vcf_path")
        proband_sex = base_state.get("proband_sex", "Unknown")
        if vcf_path and Path(vcf_path).exists():
            zygosity = _extract_zygosity_from_vcf(
                vcf_path, chrom, pos_int, ref, alt, proband_sex
            )
        else:
            logger.debug(f"VCF not found for zygosity extraction: {vcf_path}")

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

        # Phase 2 — population frequency
        "max_gnomad_af":           max_af,
        "gnomad_af_popmax":        gnomad_popmax,
        "gnomad_nhomalt":          0,    # not in VEP TSV; set by Agent 1 via tabix
        "gnomad_af_by_population": af_by_pop,

        # Phase 3 — ClinVar
        "clinvar_classification": clinvar_sig,
        "clinvar_review_stars":   clinvar_stars,
        "clinvar_disease":        clinvar_disease,
        "clinvar_accession":      clinvar_acc,

        # Phase 4 — in-silico scores
        "is_loftee_hc":           is_loftee_hc,
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
        # These require ClinVar tabix lookups — done by Agent 4
        "gene_clinvar_missense_fraction": None,
        "gene_clinvar_lof_fraction":      None,
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
            for row in reader:
                # Strip whitespace from all values
                row = {k.strip(): v.strip() for k, v in row.items() if k}

                variant_state = _parse_vep_row(
                    row, session_id, state, gnomad_constraint, clingen
                )
                if variant_state is None:
                    continue

                vid = variant_state.get("variant_id", "")
                if vid in seen_variant_ids:
                    continue    # deduplicate — one canonical row per variant

                # Filter out non-coding consequence types with no clinical significance
                consequence = variant_state.get("consequence", "")
                EXCLUDED_CONSEQUENCES = {
                    "upstream_gene_variant",
                    "downstream_gene_variant",
                    "intergenic_variant",
                    "intron_variant",
                }

                if consequence in EXCLUDED_CONSEQUENCES:
                    logger.debug(f"[{session_id}] Filtered out {vid}: {consequence}")
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
                        logger.debug(f"[{session_id}] Filtered synonymous {vid}: SpliceAI={spliceai:.3f} < 0.2, not at boundary")
                        continue

                seen_variant_ids.add(vid)
                parsed_variants.append(variant_state)

    except Exception as e:
        warnings.append(f"POST_PROCESS_ERROR: Failed to parse VEP TSV: {e}")
        logger.error(f"[{session_id}] VEP TSV parse error: {e}", exc_info=True)
        return {"warnings": warnings}

    logger.info(
        f"[{session_id}] Parsed {len(parsed_variants)} canonical variants "
        f"from {tsv_path.name}"
    )

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

