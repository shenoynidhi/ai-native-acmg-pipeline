"""
src/agents/agent2_consequence.py

Agent 2 — Consequence / Loss-of-Function Criteria
Evaluates: PVS1

ACMG/AMP 2015 + ClinGen PVS1 decision tree (Tayoun et al. 2018):
  PVS1         — Null variant in gene where LoF is a known disease mechanism
  PVS1_Strong  — Caveats reduce strength (e.g. last exon, alternate transcripts)
  PVS1_Moderate— Further reduced (e.g. LoF not well-established mechanism)
  PVS1_Supporting — Minimal confidence in LoF mechanism

The 5-caveat decision tree:
  1. Is LoF an established disease mechanism for this gene?
  2. Does the variant cause NMD (not last exon / last 50nt of penultimate exon)?
  3. Are there functional alternative transcripts that escape the variant?
  4. Is the variant in a critical functional domain?
  5. Is there prior evidence of LoF variants at this locus?

State fields read:
  consequence, is_loftee_hc, gene, gene_clingen_validity, gene_orphanet_inheritance,
  gene_gnomad_pli, gene_gnomad_loeuf, exon_number, intron_number,
  hgvsc, hgvsp, transcript, clinvar_clnsig, clinvar_stars,
  gene_clinvar_lof_fraction

State fields written (via agent_evidence):
  agent_evidence["agent2"]
"""

import logging
from src.utils.logging_config import get_user_friendly_logger
import re
from typing import Optional

from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json

logger = get_user_friendly_logger('agent2_consequence_old')

# ---------------------------------------------------------------------------
# Consequence classes that can trigger PVS1
# ---------------------------------------------------------------------------

LOF_CONSEQUENCES = {
    "stop_gained",
    "frameshift_variant",
    "splice_acceptor_variant",
    "splice_donor_variant",
    "start_lost",
    "transcript_ablation",
    "transcript_amplification",
}

SPLICE_REGION_CONSEQUENCES = {
    "splice_region_variant",
    "splice_donor_5th_base_variant",
    "splice_donor_region_variant",
    "splice_polypyrimidine_tract_variant",
}

# Constraint thresholds for LoF intolerance
PLI_CONSTRAINED  = 0.9
LOEUF_CONSTRAINED = 0.35

# ClinVar LoF fraction threshold — if >30% of P/LP variants are LoF, LoF is likely mechanism
LOF_FRACTION_THRESHOLD = 0.30


# ---------------------------------------------------------------------------
# Decision tree helper functions
# ---------------------------------------------------------------------------

def _is_lof_mechanism(
    gene: str,
    clingen_validity: Optional[str],
    inheritance: Optional[str],
    pli: Optional[float],
    loeuf: Optional[float],
    clinvar_lof_fraction: Optional[float],
) -> tuple[bool, str]:
    """
    Caveat 1: Is LoF an established disease mechanism for this gene?
    Returns (is_lof_mechanism: bool, reasoning: str)
    """
    reasons = []

    # Strong evidence: ClinGen definitive/strong with LoF in ClinVar
    if clingen_validity and clingen_validity.lower() in {"definitive", "strong"}:
        reasons.append(f"ClinGen validity: {clingen_validity}")

    # gnomAD constraint (pLI/LOEUF)
    if pli is not None and pli >= PLI_CONSTRAINED:
        reasons.append(f"pLI={pli:.3f} (≥0.9, LoF intolerant)")
    if loeuf is not None and loeuf <= LOEUF_CONSTRAINED:
        reasons.append(f"LOEUF={loeuf:.3f} (≤0.35, LoF intolerant)")

    # ClinVar LoF fraction
    if clinvar_lof_fraction is not None and clinvar_lof_fraction >= LOF_FRACTION_THRESHOLD:
        reasons.append(
            f"ClinVar LoF fraction={clinvar_lof_fraction:.2f} (≥30% P/LP are LoF)"
        )

    is_established = len(reasons) > 0
    reasoning = "; ".join(reasons) if reasons else "No evidence that LoF is disease mechanism"
    return is_established, reasoning


def _is_last_exon_or_region(exon_number: Optional[str]) -> bool:
    """
    Caveat 2 helper: Is the variant in the last exon?
    Format: "15/23" → exon 15 of 23 total.
    """
    if not exon_number:
        return False
    parts = exon_number.split("/")
    if len(parts) != 2:
        return False
    try:
        current = int(parts[0])
        total   = int(parts[1])
        return current == total
    except ValueError:
        return False


def _is_near_last_50nt_penultimate(hgvsc: Optional[str], exon_number: Optional[str]) -> bool:
    """
    Heuristic: Does the variant fall in the last 50nt of the penultimate exon?
    We use HGVS coding position. This is imprecise without transcript coordinates
    — flag for LLM review if near boundary.
    """
    # Without transcript lengths we can't compute this precisely
    # Return False here; LLM will catch edge cases
    return False


def _parse_exon_fraction(exon_number: Optional[str]) -> Optional[float]:
    """
    Returns position as fraction of transcript (e.g. exon 20/23 → 0.87).
    Used to assess whether truncation affects most of the protein.
    """
    if not exon_number:
        return None
    parts = exon_number.split("/")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) / int(parts[1])
    except (ValueError, ZeroDivisionError):
        return None


def _evaluate_pvs1_strength(
    consequence: str,
    is_loftee_hc: bool,
    is_lof_mechanism: bool,
    exon_number: Optional[str],
    intron_number: Optional[str],
    hgvsc: Optional[str],
    pli: Optional[float],
    loeuf: Optional[float],
) -> tuple[Optional[str], list[str]]:
    """
    Core PVS1 strength assignment based on the 5-caveat decision tree.

    Returns:
        (strength, caveats_triggered)
        strength: "Very_Strong" | "Strong" | "Moderate" | "Supporting" | None
        caveats: list of human-readable caveat descriptions
    """
    caveats = []

    # Not a LoF consequence at all
    if consequence not in LOF_CONSEQUENCES and consequence not in SPLICE_REGION_CONSEQUENCES:
        return None, ["Consequence is not a LoF type"]

    # LoF mechanism not established
    if not is_lof_mechanism:
        return "Supporting", ["LoF mechanism not established for this gene"]

    # Splice region — more uncertain than canonical splice site
    if consequence in SPLICE_REGION_CONSEQUENCES:
        caveats.append("Splice region variant — effect on splicing uncertain")
        return "Supporting", caveats

    # Check LOFTEE — if not HC, reduce strength
    if not is_loftee_hc:
        caveats.append("LOFTEE: not high-confidence LoF")
        return "Moderate", caveats

    # Caveat: last exon (variants here often escape NMD)
    in_last_exon = _is_last_exon_or_region(exon_number)
    if in_last_exon:
        caveats.append("Last exon — variant may escape NMD")
        # Still can be Strong if gene is constrained and critical domain in last exon
        if (pli is not None and pli >= PLI_CONSTRAINED) or \
           (loeuf is not None and loeuf <= LOEUF_CONSTRAINED):
            return "Strong", caveats
        return "Moderate", caveats

    # Assess truncation position
    exon_frac = _parse_exon_fraction(exon_number)
    if exon_frac is not None and exon_frac < 0.1:
        # Very early truncation — may affect all isoforms
        caveats.append(f"Early truncation (exon {exon_number})")

    # No caveats fired — full PVS1 Very_Strong
    if not caveats:
        return "Very_Strong", []

    return "Strong", caveats


# ---------------------------------------------------------------------------
# LLM refinement
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ACMG/AMP variant classification expert specialising in null variant
and loss-of-function evidence. You apply the ClinGen PVS1 decision tree (Tayoun et al. 2018).

Respond ONLY with a JSON object. No preamble, no markdown fences. Schema:
{
  "criteria_pathogenic": {},
  "criteria_benign": {},
  "evidence_notes": "string — 3-5 sentences explaining reasoning",
  "citations": ["list of sources"],
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "pvs1_strength": "Very_Strong" | "Strong" | "Moderate" | "Supporting" | "Not_Applied",
  "pvs1_caveats": ["list of caveats triggered"],
  "lof_mechanism_established": true | false
}

PVS1 goes in criteria_pathogenic with its strength as the value. E.g.:
  {"PVS1": "Very_Strong"}
If PVS1 does not apply, both criteria dicts should be empty."""


def _llm_refine_pvs1(state: VariantState, rule_strength: Optional[str], caveats: list) -> dict:
    """Call LLM for PVS1 cases that are ambiguous or borderline."""
    gene        = state.get("gene", "UNKNOWN")
    consequence = state.get("consequence", "")
    exon        = state.get("exon_number") or "unknown"
    intron      = state.get("intron_number") or "N/A"
    hgvsc       = state.get("hgvsc") or "unknown"
    hgvsp       = state.get("hgvsp") or "N/A"
    transcript  = state.get("transcript") or "unknown"
    pli         = state.get("gene_gnomad_pli")
    loeuf       = state.get("gene_gnomad_loeuf")
    clingen     = state.get("gene_clingen_validity") or "Unknown"
    inheritance = state.get("gene_orphanet_inheritance") or "Unknown"
    loftee_hc   = state.get("is_loftee_hc", False)
    lof_frac    = state.get("gene_clinvar_lof_fraction")
    clnsig      = state.get("clinvar_clnsig") or "Not in ClinVar"
    clnstars    = state.get("clinvar_stars", 0)

    user_prompt = f"""Evaluate PVS1 for this variant using the ClinGen PVS1 decision tree:

Gene: {gene}
Transcript: {transcript}
Consequence: {consequence}
HGVSc: {hgvsc}
HGVSp: {hgvsp}
Exon: {exon}
Intron: {intron}

Gene-level evidence:
  ClinGen validity: {clingen}
  Inheritance: {inheritance}
  gnomAD pLI: {pli}
  gnomAD LOEUF: {loeuf}
  ClinVar LoF fraction: {lof_frac}
  LOFTEE high-confidence LoF: {loftee_hc}

ClinVar for this variant: {clnsig} ({clnstars} stars)

Rule-based pre-evaluation:
  PVS1 strength assigned: {rule_strength or 'Not assigned'}
  Caveats triggered: {caveats}

Apply all 5 PVS1 caveats:
1. Is LoF an established disease mechanism?
2. Does the variant cause NMD (not last exon, not last 50nt of penultimate exon)?
3. Are there functional alternative transcripts that escape the variant?
4. Is the truncation in a critical functional domain?
5. Is there prior LoF evidence at this locus from ClinVar/literature?
"""

    return call_llm_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def agent2_consequence(state: VariantState) -> dict:
    """
    Agent 2: Evaluate PVS1 using the ClinGen 5-caveat decision tree.

    Returns:
        dict with key "agent_evidence" -> {"agent2": AgentEvidence dict}
    """
    gene       = state.get("gene", "UNKNOWN")
    variant_id = state.get("variant_id", "?")
    consequence = state.get("consequence", "") or ""
    logger.info(f"[agent2_consequence] Evaluating {variant_id} ({gene}) — {consequence}")

    criteria_p: dict = {}
    criteria_b: dict = {}
    citations = ["ACMG/AMP 2015", "ClinGen PVS1 guidelines (Tayoun et al. 2018)"]

    # Fast exit: not a LoF consequence
    if consequence not in LOF_CONSEQUENCES and consequence not in SPLICE_REGION_CONSEQUENCES:
        return {
            "agent_evidence": {
                "agent2": {
                    "criteria_pathogenic": {},
                    "criteria_benign":     {},
                    "evidence_notes":      (
                        f"PVS1 not applicable: {consequence} is not a loss-of-function "
                        f"consequence type."
                    ),
                    "citations":           citations,
                    "confidence":          "HIGH",
                }
            }
        }

    # --- Gather inputs ---
    is_loftee_hc  = state.get("is_loftee_hc", False)
    pli           = state.get("gene_gnomad_pli")
    loeuf         = state.get("gene_gnomad_loeuf")
    clingen       = state.get("gene_clingen_validity")
    inheritance   = state.get("gene_orphanet_inheritance")
    lof_fraction  = state.get("gene_clinvar_lof_fraction")
    exon_number   = state.get("exon_number")
    intron_number = state.get("intron_number")
    hgvsc         = state.get("hgvsc")

    # --- Caveat 1: LoF mechanism ---
    lof_mech, lof_mech_reason = _is_lof_mechanism(
        gene, clingen, inheritance, pli, loeuf, lof_fraction
    )

    # --- Rule-based strength assignment ---
    rule_strength, caveats = _evaluate_pvs1_strength(
        consequence, is_loftee_hc, lof_mech,
        exon_number, intron_number, hgvsc, pli, loeuf,
    )

    # --- Determine if LLM needed ---
    # Call LLM when: strength is ambiguous, multiple caveats, or splice region
    needs_llm = (
        rule_strength in {"Moderate", "Supporting"} or
        len(caveats) >= 2 or
        consequence in SPLICE_REGION_CONSEQUENCES or
        not lof_mech
    )

    if needs_llm:
        logger.debug(f"[agent2] Calling LLM for PVS1 on {variant_id}")
        llm_result = _llm_refine_pvs1(state, rule_strength, caveats)

        if llm_result and not llm_result.get("error"):
            llm_strength   = llm_result.get("pvs1_strength", "Not_Applied")
            llm_caveats    = llm_result.get("pvs1_caveats", caveats)
            evidence_notes = llm_result.get("evidence_notes", "")
            confidence     = llm_result.get("confidence", "MEDIUM")
            citations     += llm_result.get("citations", [])

            if llm_strength and llm_strength != "Not_Applied":
                criteria_p["PVS1"] = llm_strength
            caveats = llm_caveats
        else:
            logger.warning(f"[agent2] LLM failed for {variant_id} — using rule-based")
            if rule_strength:
                criteria_p["PVS1"] = rule_strength
            confidence = "LOW"
            evidence_notes = (
                f"PVS1 ({rule_strength or 'Not_Applied'}) assigned by rules. "
                f"LLM unavailable. Caveats: {'; '.join(caveats) or 'None'}. "
                f"LoF mechanism: {lof_mech_reason}."
            )
    else:
        # High-confidence rule result
        if rule_strength:
            criteria_p["PVS1"] = rule_strength
        confidence = "HIGH"
        evidence_notes = (
            f"PVS1 ({rule_strength}) applies to {gene} {consequence}. "
            f"LoF mechanism established ({lof_mech_reason}). "
            f"LOFTEE HC: {is_loftee_hc}. "
            f"Caveats: {'; '.join(caveats) or 'None'}."
        )

    # No strength assigned at all
    if not criteria_p and not criteria_b:
        evidence_notes = (
            f"PVS1 does not apply for {gene} {consequence}. "
            f"LoF mechanism evidence: {lof_mech_reason}."
        )
        confidence = "HIGH"

    citations = list(dict.fromkeys(citations))
    logger.info(
        f"[agent2] {variant_id}: P={criteria_p} B={criteria_b} conf={confidence}"
    )

    return {
        "agent_evidence": {
            "agent2": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign":     criteria_b,
                "evidence_notes":      evidence_notes,
                "citations":           citations,
                "confidence":          confidence,
            }
        }
    }
