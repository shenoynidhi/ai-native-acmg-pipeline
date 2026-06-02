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

def _float(value: str) -> Optional[float]:
    """Parse a VEP field to float. Returns None for '.', '', 'nan'."""
    if not value or value in (".", "-", "nan", "NA", "N/A"):
        return None
    try:
        return float(value)
    except ValueError:
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

    revel = _float(row.get("REVEL_score", ".") or ".")
    if revel is not None:
        if revel >= cfg_revel_path:
            dam += 1
        elif revel <= cfg_revel_ben:
            ben += 1

    cadd = _float(row.get("CADD_phred", ".") or ".")
    if cadd is not None:
        if cadd >= cfg_cadd:
            dam += 1
        else:
            ben += 1

    # PolyPhen: D/P = damaging, B = benign
    pp2 = _str(row.get("Polyphen2_HDIV_score", ".") or ".")
    # VEP --everything gives numeric score, not category
    pp2_score = _float(row.get("Polyphen2_HDIV_score", ".") or ".")
    if pp2_score is not None:
        if pp2_score >= 0.909:
            dam += 1
        elif pp2_score <= 0.446:
            ben += 1

    # SIFT: lower = more damaging (<0.05 = deleterious)
    sift = _float(row.get("SIFT_score", ".") or ".")
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
    # Normalise to chr:pos:ref:alt
    if "_" in uploaded and "/" in uploaded:
        parts = uploaded.split("_")
        if len(parts) >= 3:
            chrom, pos = parts[0], parts[1]
            ref_alt = parts[2].split("/")
            ref = ref_alt[0] if ref_alt else "."
            alt = allele
            variant_id = f"{chrom}:{pos}:{ref}:{alt}"
        else:
            variant_id = uploaded
    else:
        variant_id = f"{location}:{allele}"

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

    # In-silico scores
    spliceai    = _parse_spliceai(row.get("SpliceAI_pred", "") or "")
    revel       = _float(row.get("REVEL_score", "") or "")
    cadd        = _float(row.get("CADD_phred", "") or "")
    sift        = _float(row.get("SIFT_score", "") or "")
    pp2         = _float(row.get("Polyphen2_HDIV_score", "") or "")
    phylop      = _float(row.get("phyloP100way_vertebrate", "") or "")
    gerp        = _float(row.get("GERP++_RS", "") or "")
    metasvm     = _float(row.get("MetaSVM_score", "") or "")

    # LOFTEE
    lof_tag     = _str(row.get("LoF", "") or "")
    is_loftee_hc = lof_tag == "HC"

    # In-silico votes
    dam_votes, ben_votes = _insilico_votes(row)

    # Structural flags
    csq_set = set(consequence.split("&"))
    is_inframe = bool(csq_set & _INFRAME_CONSEQUENCES)
    is_stop_loss = "stop_lost" in csq_set

    # Protein position
    prot_pos = _int(row.get("Protein_position", "") or "")

    # Exon / intron numbers
    exon_num   = _str(row.get("EXON", "") or "")
    intron_num = _str(row.get("INTRON", "") or "")

    # HGVSc / HGVSp
    hgvsc = _str(row.get("HGVSc", "") or "")
    hgvsp = _str(row.get("HGVSp", "") or "")

    # Gene-level context from reference databases
    constraint = gnomad_constraint.get(gene, {})
    pli   = constraint.get("pLI")
    loeuf = constraint.get("loeuf")
    z     = constraint.get("z")
    clingen_val = clingen.get(gene)

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

        # Phase 2 — population frequency
        "max_gnomad_af":           max_af,
        "gnomad_af_popmax":        gnomad_popmax,
        "gnomad_nhomalt":          0,    # not in VEP TSV; set by Agent 1 via tabix
        "gnomad_af_by_population": af_by_pop,

        # Phase 3 — ClinVar
        "clinvar_clnsig":    clinvar_sig,
        "clinvar_stars":     clinvar_stars,
        "clinvar_disease":   clinvar_disease,
        "clinvar_accession": clinvar_acc,

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
