"""
src/pipeline/nodes/zygosity_filter.py

Zygosity Filter Node — decides whether a variant should be RETAIN,
DEPRIORITIZE, or RETAIN_UNCONFIRMED based on whether its observed
zygosity is consistent with the gene's expected inheritance mode.

This is the final node before report_generator. It does NOT discard
variants — it sets zygosity_filter_status which the report generator
uses for ranking and flagging.

Status values:
  RETAIN              — zygosity is fully consistent with inheritance mode;
                        include prominently in report
  DEPRIORITIZE        — zygosity is inconsistent (e.g. compound het CIS in
                        AR gene); include in report with a flag
  RETAIN_UNCONFIRMED  — zygosity could be consistent but cannot be confirmed
                        (unphased, unknown sex, unknown inheritance);
                        include with a caveat

Decision matrix:

  AR gene:
    compound_het_trans confirmed          → RETAIN
    homozygous (gnomad_nhomalt > 0
                OR phase says hom)        → RETAIN
    compound_het_cis (both on same chrom) → DEPRIORITIZE
    single het, unphased, no partner      → RETAIN_UNCONFIRMED
    phase_status = not_applicable         → RETAIN_UNCONFIRMED

  AD gene:
    heterozygous (most het calls)         → RETAIN
    homozygous                            → RETAIN (GoF variants can be hom)
    compound_het_trans                    → DEPRIORITIZE
      (two hits in AD gene usually means
       one is the pathogenic het — flag)

  XLR gene:
    male  + hemizygous / het on X         → RETAIN
    female + heterozygous                 → RETAIN_UNCONFIRMED (carrier state)
    female + homozygous                   → RETAIN
    unknown sex                           → RETAIN_UNCONFIRMED

  XLD gene:
    any heterozygous or homozygous        → RETAIN

  Mito / mtDNA:
    any (heteroplasmy handled separately) → RETAIN

  No inheritance data OR unknown:
    any                                   → RETAIN_UNCONFIRMED

  No patient HPO terms AND phenotype_score == 0.0:
    any                                   → RETAIN_UNCONFIRMED
    (phenotype data absent — cannot
     assess phenotype-zygosity fit)

Node contract:
  Input fields read:
    gene_orphanet_inheritance  (Optional[str])  e.g. "AR", "AD", "XLR", "XLD", "Mito"
    phase_status               (str)            "compound_het_trans" | "compound_het_cis" |
                                                "unphased" | "not_applicable"
    phase_confidence           (str)            "HIGH" | "MEDIUM" | "LOW"
    proband_sex                (Optional[str])  "male" | "female" | "unknown"
    gnomad_nhomalt             (int)            homozygous count in gnomAD
    patient_hpo_terms          (List[Dict])
    phenotype_score            (Optional[float])
    gene                       (str)
    variant_id                 (str)
  Output fields set:
    zygosity_filter_status     (str)   "RETAIN" | "DEPRIORITIZE" | "RETAIN_UNCONFIRMED"
    warnings                   (list)  appended (not replaced) — zygosity warnings added
"""

import logging
from typing import List, Optional

from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------

def _decide_ar(
    phase_status:     str,
    phase_confidence: str,
    gnomad_nhomalt:   int,
) -> tuple:
    """
    Returns (status, reason) for AR inheritance.
    """
    phase = phase_status.lower()

    # Confirmed compound het in trans — both copies affected
    if phase == "compound_het_trans":
        if phase_confidence == "LOW":
            return (
                "RETAIN_UNCONFIRMED",
                "AR gene: compound het trans detected but phasing confidence is LOW — "
                "confirm with parental testing.",
            )
        return (
            "RETAIN",
            "AR gene: compound het trans confirmed — both alleles affected.",
        )

    # CIS — both variants on same chromosome, one functional copy remains
    if phase == "compound_het_cis":
        return (
            "DEPRIORITIZE",
            "AR gene: compound het CIS — both variants on same chromosome; "
            "likely one functional allele remains. Deprioritised.",
        )

    # Homozygous — gnomAD nhomalt > 0 or not_applicable (single variant, hom GT)
    if gnomad_nhomalt and gnomad_nhomalt > 0:
        return (
            "RETAIN",
            f"AR gene: homozygous occurrence confirmed (gnomAD nhomalt={gnomad_nhomalt}).",
        )

    if phase == "not_applicable":
        # Single variant in AR gene — could be homozygous or het without partner
        return (
            "RETAIN_UNCONFIRMED",
            "AR gene: phase not applicable — single variant; homozygosity or "
            "second hit not confirmed. Parental testing recommended.",
        )

    # Unphased het — second hit unknown
    return (
        "RETAIN_UNCONFIRMED",
        "AR gene: single heterozygous variant, unphased — second hit not confirmed. "
        "Consider parental segregation.",
    )


def _decide_ad(phase_status: str) -> tuple:
    """Returns (status, reason) for AD inheritance."""
    phase = phase_status.lower()

    if phase == "compound_het_trans":
        return (
            "DEPRIORITIZE",
            "AD gene: compound het trans detected — two hits in an AD gene is unusual; "
            "one variant may be incidental. Review individually.",
        )
    # Heterozygous or homozygous — both consistent with AD
    return (
        "RETAIN",
        "AD gene: heterozygous/homozygous genotype consistent with autosomal dominant.",
    )


def _decide_xlr(
    proband_sex:    Optional[str],
    phase_status:   str,
    gnomad_nhomalt: int,
) -> tuple:
    """Returns (status, reason) for XLR inheritance."""
    sex   = (proband_sex or "unknown").lower()
    phase = phase_status.lower()

    if sex == "unknown":
        return (
            "RETAIN_UNCONFIRMED",
            "XLR gene: proband sex unknown — cannot confirm hemizygosity. "
            "Add proband_sex to enable full zygosity assessment.",
        )

    if sex == "male":
        # Males are hemizygous on X — a single het call is effectively hemizygous
        return (
            "RETAIN",
            "XLR gene: male proband — hemizygous on X chromosome; consistent with XLR.",
        )

    # Female
    if gnomad_nhomalt and gnomad_nhomalt > 0:
        return (
            "RETAIN",
            "XLR gene: female proband, homozygous — consistent with XLR affected female.",
        )
    return (
        "RETAIN_UNCONFIRMED",
        "XLR gene: female proband, heterozygous — likely carrier state; "
        "affected status requires homozygosity or skewed X-inactivation evidence.",
    )


def _decide_xld(phase_status: str) -> tuple:
    """Returns (status, reason) for XLD inheritance."""
    return (
        "RETAIN",
        "XLD gene: heterozygous/homozygous genotype consistent with X-linked dominant.",
    )


def _decide_mito() -> tuple:
    """Returns (status, reason) for mitochondrial inheritance."""
    return (
        "RETAIN",
        "Mitochondrial gene: variant retained — heteroplasmy assessment outside "
        "scope of zygosity filter.",
    )


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

def zygosity_filter_node(state: VariantState) -> dict:
    """
    LangGraph node: set zygosity_filter_status and append zygosity warnings.

    Returns:
        {
            "zygosity_filter_status": str,
            "warnings":               list,   # existing warnings + any new ones
        }
    """
    inheritance:      Optional[str] = state.get("gene_orphanet_inheritance")
    phase_status:     str           = (state.get("phase_status")     or "not_applicable").strip()
    phase_confidence: str           = (state.get("phase_confidence") or "LOW").strip().upper()
    proband_sex:      Optional[str] = state.get("proband_sex")
    gnomad_nhomalt:   int           = state.get("gnomad_nhomalt") or 0
    patient_hpo_terms: List[dict]   = state.get("patient_hpo_terms") or []
    phenotype_score:  Optional[float] = state.get("phenotype_score")
    gene:             str           = (state.get("gene")       or "").strip()
    variant_id:       str           = (state.get("variant_id") or "").strip()

    existing_warnings: list = list(state.get("warnings") or [])
    new_warnings:      list = []

    # ---- No phenotype data at all -------------------------------------------
    # If neither HPO terms nor a phenotype score exist, the zygosity assessment
    # loses its phenotype context — flag but don't fully deprioritise.
    no_phenotype = (not patient_hpo_terms) and (not phenotype_score or phenotype_score == 0.0)

    # ---- Normalise inheritance string --------------------------------------
    inh = (inheritance or "").strip().upper()

    # ---- Route to inheritance-specific decision ----------------------------
    if not inh or inh in ("UNKNOWN", ""):
        status = "RETAIN_UNCONFIRMED"
        reason = (
            f"Gene {gene}: no inheritance mode on record — "
            "zygosity consistency cannot be assessed."
        )

    elif inh == "AR":
        status, reason = _decide_ar(phase_status, phase_confidence, gnomad_nhomalt)

    elif inh == "AD":
        status, reason = _decide_ad(phase_status)

    elif inh == "XLR":
        status, reason = _decide_xlr(proband_sex, phase_status, gnomad_nhomalt)

    elif inh == "XLD":
        status, reason = _decide_xld(phase_status)

    elif inh in ("MITO", "MITOCHONDRIAL", "MT"):
        status, reason = _decide_mito()

    else:
        # Covers "Digenic", "Multifactorial", compound modes etc.
        status = "RETAIN_UNCONFIRMED"
        reason = (
            f"Gene {gene}: inheritance mode '{inheritance}' not handled by "
            "zygosity filter — retaining with unconfirmed status."
        )

    # ---- Override to RETAIN_UNCONFIRMED if no phenotype context ------------
    if no_phenotype and status == "RETAIN":
        status = "RETAIN_UNCONFIRMED"
        new_warnings.append(
            f"{variant_id}: zygosity status downgraded from RETAIN to "
            "RETAIN_UNCONFIRMED — no patient HPO terms or phenotype score available."
        )

    # ---- Log and collect warnings ------------------------------------------
    logger.info(
        f"zygosity_filter: {variant_id} | gene={gene} | inh={inh} | "
        f"phase={phase_status} | sex={proband_sex} | "
        f"status={status} | reason={reason}"
    )

    if status == "DEPRIORITIZE":
        new_warnings.append(f"{variant_id}: DEPRIORITIZED — {reason}")
    elif status == "RETAIN_UNCONFIRMED":
        new_warnings.append(f"{variant_id}: RETAIN_UNCONFIRMED — {reason}")

    return {
        "zygosity_filter_status": status,
        "warnings":               existing_warnings + new_warnings,
    }
