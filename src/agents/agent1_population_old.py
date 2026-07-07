"""
src/agents/agent1_population.py

Agent 1 — Population Frequency Criteria
Evaluates: BA1, BS1, BS2, PM2

ACMG/AMP 2015 criteria assessed:
  BA1  — Allele frequency > 5% in gnomAD → Benign standalone (short-circuit)
  BS1  — AF > disorder-specific threshold (default 1%) → Benign Strong
  BS2  — Observed in healthy adult homozygotes in gnomAD (for recessive/AD disease)
  PM2  — Absent or extremely low AF in gnomAD (<0.0001) → Pathogenic Moderate
         PM2_Supporting if AF < 0.001 (ClinGen refinement)

State fields read:
  max_gnomad_af, gnomad_af_popmax, gnomad_nhomalt, gnomad_af_by_population,
  gene, consequence, gene_clingen_validity, gene_orphanet_inheritance

State fields written (via agent_evidence):
  agent_evidence["agent1"]
"""

import logging
from src.utils.logging_config import get_user_friendly_logger
from typing import Optional

from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json

logger = get_user_friendly_logger('agent1_population')

# ---------------------------------------------------------------------------
# Thresholds (per ACMG/AMP 2015 + ClinGen SVI 2018 refinements)
# ---------------------------------------------------------------------------

BA1_THRESHOLD    = 0.05    # >5% → Benign standalone
BS1_THRESHOLD    = 0.01    # >1% → Benign Strong (general; adjust per disorder)
PM2_THRESHOLD    = 0.0001  # <0.01% → PM2 Moderate
PM2_SUP_THRESHOLD = 0.001  # <0.1% → PM2 Supporting (ClinGen refinement)

# Minimum homozygote count to trigger BS2
BS2_HOMALT_MIN   = 2


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _evaluate_ba1(max_af: float, popmax_af: float) -> Optional[str]:
    """BA1: AF > 5% in any gnomAD population → Benign standalone."""
    if max_af > BA1_THRESHOLD or popmax_af > BA1_THRESHOLD:
        return "Benign_Standalone"
    return None


def _evaluate_bs1(max_af: float, popmax_af: float, inheritance: Optional[str]) -> Optional[str]:
    """
    BS1: AF higher than expected for the disorder.
    Threshold is loosened slightly for AR disorders (carrier frequency can be higher).
    """
    threshold = BS1_THRESHOLD
    if inheritance and "AR" in inheritance.upper():
        threshold = 0.02  # AR: 2% carrier frequency is plausible

    if max_af > threshold or popmax_af > threshold:
        return "Benign_Strong"
    return None


def _evaluate_bs2(
    nhomalt: int,
    inheritance: Optional[str],
    consequence: str,
    clingen_validity: Optional[str],
) -> Optional[str]:
    """
    BS2: Observed homozygous in gnomAD for a fully penetrant disorder.
    Only applies when:
      - Gene has established disease association (ClinGen Definitive/Strong)
      - Variant is not in a recessive gene (homozygotes expected for carriers)
      - Not a benign/synonymous consequence
    """
    if nhomalt < BS2_HOMALT_MIN:
        return None

    # Don't apply BS2 for clearly AR inheritance — homozygotes expected
    if inheritance and "AR" in inheritance.upper():
        return None

    # Don't apply to synonymous or intronic variants
    benign_consequences = {
        "synonymous_variant", "intron_variant",
        "upstream_gene_variant", "downstream_gene_variant",
        "3_prime_UTR_variant", "5_prime_UTR_variant",
    }
    if consequence in benign_consequences:
        return None

    # Only meaningful if gene has a known disease association
    if clingen_validity and clingen_validity.lower() in {
        "definitive", "strong", "moderate"
    }:
        return "Benign_Strong"

    return None


def _evaluate_pm2(
    max_af: float,
    popmax_af: float,
    af_by_pop: dict,
    consequence: str,
) -> Optional[str]:
    """
    PM2: Absent or extremely rare in gnomAD.
    Returns "Moderate" if < 0.01%, "Supporting" if < 0.1%.
    Not applied to common benign consequence types.
    """
    # Don't apply PM2 to clearly non-coding/benign consequence classes
    skip_consequences = {
        "synonymous_variant", "intron_variant",
        "upstream_gene_variant", "downstream_gene_variant",
        "3_prime_UTR_variant", "5_prime_UTR_variant",
        "regulatory_region_variant",
    }
    if consequence in skip_consequences:
        return None

    af_to_check = max(max_af, popmax_af, max(af_by_pop.values(), default=0.0))

    if af_to_check == 0.0:
        # Completely absent — strongest PM2 (Moderate)
        return "Moderate"
    if af_to_check < PM2_THRESHOLD:
        return "Moderate"
    if af_to_check < PM2_SUP_THRESHOLD:
        return "Supporting"

    return None


# ---------------------------------------------------------------------------
# LLM refinement — only called when criteria are borderline
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ACMG/AMP variant classification expert specialising in population
frequency evidence. You will be given gnomAD allele frequency data and gene context for a variant.
Your task is to evaluate whether BA1, BS1, BS2, or PM2 apply, following ACMG 2015 guidelines and
ClinGen SVI refinements.

Respond ONLY with a JSON object. No preamble, no markdown fences. Schema:
{
  "criteria_pathogenic": {},
  "criteria_benign": {},
  "evidence_notes": "string — 2-4 sentences explaining your reasoning",
  "citations": ["list of sources used"],
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "llm_modified_criteria": true | false
}

Keys in criteria_pathogenic / criteria_benign are criterion codes (e.g. "PM2"),
values are strength strings (e.g. "Moderate", "Supporting", "Strong", "Standalone").
Only include criteria that apply. Empty dict if none."""


def _llm_refine(state: VariantState, rule_criteria_p: dict, rule_criteria_b: dict) -> dict:
    """
    Call LLM only for borderline cases — when AF is near a threshold or
    inheritance pattern creates ambiguity. For clear-cut cases, skip LLM.
    """
    max_af    = state.get("max_gnomad_af", 0.0)
    popmax_af = state.get("gnomad_af_popmax", 0.0)
    nhomalt   = state.get("gnomad_nhomalt", 0)
    gene      = state.get("gene", "UNKNOWN")
    consequence = state.get("consequence", "")
    inheritance = state.get("gene_orphanet_inheritance", "Unknown")
    clingen   = state.get("gene_clingen_validity", "Unknown")
    pli       = state.get("gene_gnomad_pli", None)
    loeuf     = state.get("gene_gnomad_loeuf", None)
    af_by_pop = state.get("gnomad_af_by_population", {})

    user_prompt = f"""Evaluate population frequency evidence for this variant:

Gene: {gene}
Consequence: {consequence}
Inheritance (Orphanet): {inheritance}
ClinGen disease validity: {clingen}
gnomAD pLI: {pli}
gnomAD LOEUF: {loeuf}

gnomAD allele frequencies:
  max AF across populations: {max_af:.8f}
  popmax AF: {popmax_af:.8f}
  nhomalt (homozygotes): {nhomalt}
  AF by population: {af_by_pop}

Rule-based pre-evaluation (may be incomplete for borderline cases):
  Criteria pathogenic: {rule_criteria_p}
  Criteria benign: {rule_criteria_b}

Please evaluate BA1, BS1, BS2, and PM2 and return your final assessment.
Pay special attention to:
- Whether gnomAD has sufficient coverage for this variant (low nhomalt may indicate
  a low-coverage region, weakening BA1/BS1)
- Whether the inheritance pattern is consistent with observed homozygotes (BS2)
- Whether PM2 should be Moderate vs Supporting given gene constraint
"""

    result = call_llm_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)
    return result


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def agent1_population(state: VariantState) -> dict:
    """
    Agent 1: Evaluate population frequency criteria (BA1, BS1, BS2, PM2).

    Returns:
        dict with key "agent_evidence" -> {"agent1": AgentEvidence dict}
    """
    gene        = state.get("gene", "UNKNOWN")
    variant_id  = state.get("variant_id", "?")
    logger.info(f" Evaluating {variant_id} ({gene})")

    max_af      = state.get("max_gnomad_af", 0.0) or 0.0
    popmax_af   = state.get("gnomad_af_popmax", 0.0) or 0.0
    nhomalt     = state.get("gnomad_nhomalt", 0) or 0
    af_by_pop   = state.get("gnomad_af_by_population", {}) or {}
    consequence = state.get("consequence", "") or ""
    inheritance = state.get("gene_orphanet_inheritance") or ""
    clingen     = state.get("gene_clingen_validity") or ""

    # --- Rule-based evaluation ---
    criteria_p: dict = {}
    criteria_b: dict = {}
    notes_parts: list[str] = []
    citations   = ["gnomAD v3.1", "ACMG/AMP 2015", "ClinGen SVI 2018"]

    ba1 = _evaluate_ba1(max_af, popmax_af)
    if ba1:
        criteria_b["BA1"] = ba1
        notes_parts.append(
            f"BA1 fires: max gnomAD AF={max_af:.4f}, popmax={popmax_af:.4f} "
            f"(threshold >5%). Variant is common — Benign standalone."
        )

    # Only evaluate other criteria if BA1 didn't fire
    if not ba1:
        bs1 = _evaluate_bs1(max_af, popmax_af, inheritance)
        if bs1:
            criteria_b["BS1"] = bs1
            notes_parts.append(
                f"BS1 fires: AF={max_af:.4f} exceeds disorder-specific threshold "
                f"(inheritance={inheritance or 'unknown'})."
            )

        bs2 = _evaluate_bs2(nhomalt, inheritance, consequence, clingen)
        if bs2:
            criteria_b["BS2"] = bs2
            notes_parts.append(
                f"BS2 fires: {nhomalt} homozygotes in gnomAD for gene with "
                f"ClinGen validity={clingen or 'unknown'}, inheritance={inheritance or 'unknown'}."
            )

        pm2 = _evaluate_pm2(max_af, popmax_af, af_by_pop, consequence)
        if pm2:
            criteria_p["PM2"] = pm2
            notes_parts.append(
                f"PM2 fires ({pm2}): AF={max_af:.8f} is extremely low/absent in gnomAD."
            )

    # --- Determine if LLM refinement is needed ---
    # Skip LLM for clear-cut cases to save latency
    is_borderline = (
        (PM2_THRESHOLD <= max_af <= PM2_SUP_THRESHOLD) or   # near PM2 threshold
        (BS1_THRESHOLD * 0.5 <= max_af <= BS1_THRESHOLD * 2) or  # near BS1 threshold
        (nhomalt == 1) or                                    # single homozygote — ambiguous
        (not criteria_p and not criteria_b)                  # no criteria fired — LLM check
    )

    if is_borderline:
        logger.debug(f" Borderline case for {variant_id} — calling LLM")
        llm_result = _llm_refine(state, criteria_p, criteria_b)

        if llm_result and not llm_result.get("error"):
            # LLM takes precedence for borderline cases
            criteria_p = llm_result.get("criteria_pathogenic", criteria_p)
            criteria_b = llm_result.get("criteria_benign", criteria_b)
            confidence = llm_result.get("confidence", "MEDIUM")
            evidence_notes = llm_result.get("evidence_notes", " ".join(notes_parts))
            citations += llm_result.get("citations", [])
            if llm_result.get("llm_modified_criteria"):
                logger.info(f" LLM modified criteria for {variant_id}")
        else:
            logger.warning(f" LLM call failed for {variant_id} — using rule-based only")
            confidence = "MEDIUM"
            evidence_notes = " ".join(notes_parts) or (
                f"No population frequency criteria fired for {gene}. "
                f"AF={max_af:.6f}, nhomalt={nhomalt}."
            )
    else:
        # High-confidence rule-based result — no LLM needed
        confidence = "HIGH"
        evidence_notes = " ".join(notes_parts) or (
            f"No population frequency criteria apply for {gene}. "
            f"AF={max_af:.6f}, nhomalt={nhomalt}."
        )

    # Deduplicate citations
    citations = list(dict.fromkeys(citations))

    logger.info(
        f"[agent1] {variant_id}: P={criteria_p} B={criteria_b} conf={confidence}"
    )

    return {
        "agent_evidence": {
            "agent1": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign":     criteria_b,
                "evidence_notes":      evidence_notes,
                "citations":           citations,
                "confidence":          confidence,
            }
        }
    }
