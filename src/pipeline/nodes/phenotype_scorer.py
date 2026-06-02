"""
src/pipeline/nodes/phenotype_scorer.py

Phenotype Scorer Node — produces phenotype_score (0.0–1.0) and
phenotype_score_notes (str) for each variant.

Scoring components (max possible = 0.95, hard ceil at 1.0):

  1. BASE SCORE          0.0–0.55
     Weighted Jaccard between present patient HPO terms and HPO terms of
     the best-matched Orphanet disease (from orphanet_id).
     Confidence weights: HIGH=1.0, MEDIUM=0.7, LOW=0.4

  2. GENE MATCH BONUS    +0.10
     Gene appears in hpo_matched_genes (independently linked to patient
     phenotype via HPO→disease→gene traversal).

  3. DISEASE SPECIFICITY BONUS  +0.08
     Gene has exactly one associated Orphanet disease — high specificity,
     only one plausible disease explanation for this gene.

  4. CLINGEN VALIDITY BONUS  +0.12
     gene_clingen_validity is "Definitive" or "Strong" — established
     gene-disease relationship reduces prior uncertainty.
     "Moderate" gets half bonus (+0.06).

  5. INHERITANCE MATCH BONUS  +0.10
     Detected genotype matches expected inheritance mode:
       AR gene  + phase_status=compound_het_trans  OR  gnomad_nhomalt>0 → match
       AD gene  + heterozygous call (not hom)                           → match
       XLR gene + male proband (proband_sex="male")                     → match
       XLD gene + any genotype                                          → match
     XLR + unknown sex → no bonus, warning added to notes.

  6. NEGATION PENALTY    −0.05 per term, max −0.15
     Patient HPO terms marked present=False that appear in matched
     disease HPO set — phenotype mismatch evidence.

Node contract:
  Input fields read:
    patient_hpo_terms      (List[Dict])
    gene                   (str)
    orphanet_id            (Optional[str])
    hpo_matched_genes      (list)
    gene_orphanet_diseases (list)
    gene_clingen_validity  (Optional[str])
    gene_orphanet_inheritance (Optional[str])
    phase_status           (str)
    proband_sex            (Optional[str])
    gnomad_nhomalt         (int)
  Output fields set:
    phenotype_score        (float)   0.0–1.0
    phenotype_score_notes  (str)     human-readable explanation
"""

import logging
from typing import Dict, List, Optional, Set

from src.pipeline.state import VariantState
from src.pipeline.nodes.hpo_matcher import _DISEASE_TO_HPOS, _load_hpoa

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weights and caps
# ---------------------------------------------------------------------------

_CONF_WEIGHT: Dict[str, float] = {
    "HIGH":   1.0,
    "MEDIUM": 0.7,
    "LOW":    0.4,
}

_MAX_BASE_SCORE             = 0.55
_GENE_MATCH_BONUS           = 0.10
_DISEASE_SPECIFICITY_BONUS  = 0.08
_CLINGEN_DEFINITIVE_BONUS   = 0.12
_CLINGEN_MODERATE_BONUS     = 0.06
_INHERITANCE_MATCH_BONUS    = 0.10
_NEGATION_PENALTY_PER_TERM  = 0.05
_MAX_NEGATION_PENALTY       = 0.15

# Score interpretation tiers
_TIERS = [
    (0.70, "Strong phenotype support"),
    (0.40, "Moderate phenotype support"),
    (0.10, "Weak phenotype support"),
    (0.00, "No meaningful phenotype support"),
]


def _tier(score: float) -> str:
    for threshold, label in _TIERS:
        if score >= threshold:
            return label
    return "No meaningful phenotype support"


# ---------------------------------------------------------------------------
# Component 1 — weighted Jaccard
# ---------------------------------------------------------------------------

def _weighted_jaccard(
    patient_terms:   List[dict],
    disease_hpo_ids: Set[str],
) -> float:
    """
    Weighted Jaccard: Σ(weight_i for i in intersection) /
                      (Σ(weight_i for patient terms) + |disease_only terms|)
    """
    if not patient_terms or not disease_hpo_ids:
        return 0.0

    present_terms = [t for t in patient_terms if t.get("present", True) and t.get("hpo_id")]
    if not present_terms:
        return 0.0

    patient_hpo_ids      = {t["hpo_id"] for t in present_terms}
    weighted_intersection = 0.0
    patient_weight_total  = 0.0

    for term in present_terms:
        conf   = str(term.get("confidence", "MEDIUM")).upper()
        weight = _CONF_WEIGHT.get(conf, 0.7)
        patient_weight_total += weight
        if term["hpo_id"] in disease_hpo_ids:
            weighted_intersection += weight

    disease_only = len(disease_hpo_ids - patient_hpo_ids)
    denominator  = patient_weight_total + disease_only

    return weighted_intersection / denominator if denominator > 0 else 0.0


# ---------------------------------------------------------------------------
# Component 6 — negation penalty
# ---------------------------------------------------------------------------

def _negation_penalty(
    patient_terms:   List[dict],
    disease_hpo_ids: Set[str],
) -> tuple:
    """Returns (penalty_float, list_of_mismatched_term_labels)."""
    mismatches = []
    for term in patient_terms:
        if term.get("present", True):
            continue
        if term.get("hpo_id") in disease_hpo_ids:
            mismatches.append(term.get("label", term.get("hpo_id", "")))

    penalty = min(len(mismatches) * _NEGATION_PENALTY_PER_TERM, _MAX_NEGATION_PENALTY)
    return penalty, mismatches


# ---------------------------------------------------------------------------
# Component 4 — ClinGen validity bonus
# ---------------------------------------------------------------------------

def _clingen_bonus(validity: Optional[str]) -> tuple:
    """Returns (bonus_float, note_str)."""
    if not validity:
        return 0.0, ""
    v = validity.strip().lower()
    if v in ("definitive", "strong"):
        return _CLINGEN_DEFINITIVE_BONUS, f"ClinGen {validity} gene-disease validity (+{_CLINGEN_DEFINITIVE_BONUS})"
    if v == "moderate":
        return _CLINGEN_MODERATE_BONUS, f"ClinGen Moderate gene-disease validity (+{_CLINGEN_MODERATE_BONUS})"
    return 0.0, ""


# ---------------------------------------------------------------------------
# Component 5 — inheritance match bonus
# ---------------------------------------------------------------------------

def _inheritance_bonus(
    inheritance: Optional[str],
    phase_status: str,
    proband_sex:  Optional[str],
    gnomad_nhomalt: int,
) -> tuple:
    """
    Returns (bonus_float, note_str, warning_str).
    warning_str is non-empty when sex is unknown for XLR check.
    """
    if not inheritance:
        return 0.0, "", ""

    inh   = inheritance.strip().upper()
    phase = (phase_status or "").strip().lower()
    sex   = (proband_sex  or "unknown").strip().lower()

    # AR — expect compound het trans OR homozygous
    if inh == "AR":
        if phase == "compound_het_trans":
            return (
                _INHERITANCE_MATCH_BONUS,
                f"AR inheritance + compound het trans confirmed (+{_INHERITANCE_MATCH_BONUS})",
                "",
            )
        if gnomad_nhomalt and gnomad_nhomalt > 0:
            return (
                _INHERITANCE_MATCH_BONUS,
                f"AR inheritance + homozygous occurrence confirmed (+{_INHERITANCE_MATCH_BONUS})",
                "",
            )
        return 0.0, "", ""

    # AD — expect heterozygous (not compound het trans or hom)
    if inh == "AD":
        if phase not in ("compound_het_trans",):
            return (
                _INHERITANCE_MATCH_BONUS,
                f"AD inheritance + heterozygous genotype consistent (+{_INHERITANCE_MATCH_BONUS})",
                "",
            )
        return 0.0, "", ""

    # XLR — male proband hemizygous
    if inh == "XLR":
        if sex == "unknown":
            return (
                0.0,
                "",
                "XLR gene: proband_sex unknown — inheritance match bonus skipped; "
                "add proband_sex to confirm hemizygosity.",
            )
        if sex == "male":
            return (
                _INHERITANCE_MATCH_BONUS,
                f"XLR inheritance + male proband (hemizygous) (+{_INHERITANCE_MATCH_BONUS})",
                "",
            )
        return 0.0, "", ""

    # XLD — any genotype qualifies
    if inh == "XLD":
        return (
            _INHERITANCE_MATCH_BONUS,
            f"XLD inheritance + genotype consistent (+{_INHERITANCE_MATCH_BONUS})",
            "",
        )

    return 0.0, "", ""


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

def phenotype_scorer_node(state: VariantState) -> dict:
    """
    LangGraph node: score phenotype relevance for this variant.

    Returns:
        {
            "phenotype_score":       float,   # 0.0–1.0
            "phenotype_score_notes": str,     # human-readable explanation
        }
    """
    _load_hpoa()

    patient_hpo_terms:      List[dict]     = state.get("patient_hpo_terms")      or []
    gene:                   str            = (state.get("gene") or "").strip().upper()
    orphanet_id:            Optional[str]  = state.get("orphanet_id")
    hpo_matched_genes:      list           = state.get("hpo_matched_genes")       or []
    gene_orphanet_diseases: list           = state.get("gene_orphanet_diseases")  or []
    gene_clingen_validity:  Optional[str]  = state.get("gene_clingen_validity")
    gene_orphanet_inheritance: Optional[str] = state.get("gene_orphanet_inheritance")
    phase_status:           str            = state.get("phase_status")            or ""
    proband_sex:            Optional[str]  = state.get("proband_sex")
    gnomad_nhomalt:         int            = state.get("gnomad_nhomalt")          or 0

    notes_parts: List[str] = []
    warnings:    List[str] = []

    # ---- No HPO terms -------------------------------------------------------
    if not patient_hpo_terms:
        note = "No patient HPO terms available — phenotype scoring skipped."
        logger.info(f"phenotype_scorer: {gene} → score=0.0 ({note})")
        return {"phenotype_score": 0.0, "phenotype_score_notes": note}

    # ---- No match at all → floor --------------------------------------------
    gene_upper      = gene.upper()
    matched_genes_upper = [g.upper() for g in hpo_matched_genes]
    gene_in_matched = gene_upper in matched_genes_upper

    if not orphanet_id and not gene_in_matched:
        note = (
            f"Gene {gene} has no HPO-overlapping Orphanet disease and does not "
            f"appear in HPO-matched gene list — score floored at 0.0."
        )
        logger.info(f"phenotype_scorer: {note}")
        return {"phenotype_score": 0.0, "phenotype_score_notes": note}

    # ---- Disease HPO set for this variant's best-matched disease ------------
    disease_hpo_ids: Set[str] = set()
    if orphanet_id:
        disease_hpo_ids = _DISEASE_TO_HPOS.get(orphanet_id, set())

    # ---- Component 1: Base score --------------------------------------------
    base_score = 0.0
    if disease_hpo_ids:
        raw_j      = _weighted_jaccard(patient_hpo_terms, disease_hpo_ids)
        base_score = raw_j * _MAX_BASE_SCORE
        present_count = sum(
            1 for t in patient_hpo_terms
            if t.get("present", True) and t.get("hpo_id") in disease_hpo_ids
        )
        total_present = sum(1 for t in patient_hpo_terms if t.get("present", True))
        notes_parts.append(
            f"HPO overlap: {present_count}/{total_present} patient terms match "
            f"{state.get('matched_orphanet_disease', orphanet_id)} "
            f"(weighted Jaccard={raw_j:.2f}, base={base_score:.3f})"
        )
    elif gene_in_matched:
        base_score = 0.05
        notes_parts.append(
            f"Gene {gene} is HPO-linked but no direct disease HPO overlap scored "
            f"(minimal base=0.05)"
        )

    # ---- Component 2: Gene match bonus --------------------------------------
    gene_bonus = 0.0
    if gene_in_matched:
        gene_bonus = _GENE_MATCH_BONUS
        notes_parts.append(
            f"Gene {gene} independently linked to patient phenotype via "
            f"HPO→disease traversal (+{_GENE_MATCH_BONUS})"
        )

    # ---- Component 3: Disease specificity bonus -----------------------------
    specificity_bonus = 0.0
    if orphanet_id and len(gene_orphanet_diseases) == 1:
        specificity_bonus = _DISEASE_SPECIFICITY_BONUS
        notes_parts.append(
            f"Gene {gene} has only one associated Orphanet disease — "
            f"high specificity (+{_DISEASE_SPECIFICITY_BONUS})"
        )

    # ---- Component 4: ClinGen validity bonus --------------------------------
    clingen_bonus, clingen_note = _clingen_bonus(gene_clingen_validity)
    if clingen_note:
        notes_parts.append(clingen_note)

    # ---- Component 5: Inheritance match bonus --------------------------------
    inh_bonus, inh_note, inh_warning = _inheritance_bonus(
        gene_orphanet_inheritance, phase_status, proband_sex, gnomad_nhomalt
    )
    if inh_note:
        notes_parts.append(inh_note)
    if inh_warning:
        warnings.append(inh_warning)

    # ---- Component 6: Negation penalty --------------------------------------
    penalty, mismatches = _negation_penalty(patient_hpo_terms, disease_hpo_ids)
    if mismatches:
        notes_parts.append(
            f"Phenotype mismatch: patient lacks "
            f"{', '.join(mismatches[:3])}{'...' if len(mismatches) > 3 else ''} "
            f"(expected in {state.get('matched_orphanet_disease', orphanet_id)}) "
            f"(-{penalty:.2f})"
        )

    # ---- Aggregate ----------------------------------------------------------
    raw_score   = base_score + gene_bonus + specificity_bonus + clingen_bonus + inh_bonus - penalty
    final_score = round(max(0.0, min(1.0, raw_score)), 4)
    tier        = _tier(final_score)

    # Build final notes string
    notes_parts.insert(0, f"Score: {final_score} — {tier}")
    if warnings:
        notes_parts.append("WARNINGS: " + "; ".join(warnings))
    phenotype_score_notes = " | ".join(notes_parts)

    logger.info(
        f"phenotype_scorer: gene={gene}, base={base_score:.3f}, "
        f"gene_bonus={gene_bonus}, specificity={specificity_bonus}, "
        f"clingen={clingen_bonus}, inheritance={inh_bonus}, "
        f"penalty=-{penalty:.2f} → score={final_score} ({tier})"
    )

    return {
        "phenotype_score":       final_score,
        "phenotype_score_notes": phenotype_score_notes,
    }
