"""
src/agents/agent6_segregation.py

Agent 6 — Segregation / Phase Evidence
Evaluates: PP1, PM3, BP2, BS4

ACMG/AMP 2015 criteria assessed:
  PP1  — Variant co-segregates with disease in multiple affected family members.
         Pathogenic Supporting (upgradeable to Moderate/Strong with LOD scores).
         SOLO: not evaluable — flagged as limitation.
         TRIO: partial — can assess if variant is de novo vs inherited (proxy only).

  PM3  — For recessive disorders: variant detected in trans with a pathogenic variant.
         Pathogenic Moderate.
         SOLO: evaluable if phase_status = "compound_het_trans" from phasing node.
         TRIO: evaluable if parental genotypes confirm trans configuration.

  BP2  — Observed in trans with a pathogenic variant (for dominant) OR
         observed in cis with a pathogenic variant (for recessive).
         Benign Supporting.
         SOLO: evaluable if phase_status = "compound_het_cis" from phasing node.
         TRIO: evaluable from parental genotypes.

  BS4  — Lack of segregation in affected members.
         Benign Strong.
         SOLO: not evaluable.
         TRIO: partial proxy only — if variant absent in affected parent.

Solo mode limitations (always flagged):
  PP1 and BS4 require multi-generation family data → not assignable in solo mode.
  PM3 and BP2 can be inferred from phasing node output when WhatsHap ran successfully.

State fields read:
  gene, variant_id, consequence,
  phase_status, phase_confidence, phase_partner,
  clinvar_classification, clinvar_review_stars,
  gene_clingen_validity, gene_orphanet_inheritance,
  trio_mode, parent1_genotype, parent2_genotype, denovo_status

State fields written (via agent_evidence):
  agent_evidence["agent6"]
"""

import logging
from src.utils.logging_config import get_user_friendly_logger
from typing import Optional

from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json

logger = get_user_friendly_logger('agent6_segregation')

# ---------------------------------------------------------------------------
# Phase status constants (set by phasing_node using WhatsHap)
# ---------------------------------------------------------------------------

PHASE_TRANS = "compound_het_trans"   # two variants on opposite chromosomes → PM3
PHASE_CIS   = "compound_het_cis"     # two variants on same chromosome → BP2
PHASE_UNPHASED = "unphased"
PHASE_NA    = "not_applicable"


# ---------------------------------------------------------------------------
# PM3 evaluation
# ---------------------------------------------------------------------------

def _evaluate_pm3(
    phase_status: str,
    phase_confidence: str,
    phase_partner: Optional[str],
    inheritance: Optional[str],
    trio_mode: bool,
    parent1_gt: Optional[str],
    parent2_gt: Optional[str],
) -> tuple[Optional[str], list[str]]:
    """
    PM3: Variant in trans with a known pathogenic variant (recessive disorders).

    Solo: relies on WhatsHap phase_status = compound_het_trans.
    Trio: parental genotypes can confirm trans configuration directly.

    Returns (strength, notes).
    """
    notes = []

    # PM3 only meaningful for AR/recessive inheritance
    is_recessive = inheritance and "AR" in inheritance.upper()
    if not is_recessive:
        notes.append(
            f"PM3 not evaluated: inheritance={inheritance or 'unknown'} "
            f"(PM3 applies to autosomal recessive disorders only)."
        )
        return None, notes

    # --- Trio mode: parental genotypes confirm trans ---
    if trio_mode and parent1_gt and parent2_gt:
        # Trans = each parent carries one of the two variants (0/1 each)
        # This variant in parent1 AND partner variant in parent2 (or vice versa)
        # We can only confirm THIS variant's parental origin here
        p1_het = parent1_gt in {"0/1", "0|1", "1|0"}
        p2_het = parent2_gt in {"0/1", "0|1", "1|0"}
        p1_ref = parent1_gt in {"0/0", "0|0"}
        p2_ref = parent2_gt in {"0/0", "0|0"}

        if (p1_het and p2_ref) or (p2_het and p1_ref):
            # Variant inherited from one parent — consistent with trans if partner
            # variant is in the other parent (we can't confirm partner here)
            strength = "Moderate"
            notes.append(
                f"PM3 (Moderate): Trio genotypes show variant inherited from one parent "
                f"(P1={parent1_gt}, P2={parent2_gt}). Consistent with trans configuration "
                f"if partner pathogenic variant confirmed in other parent. "
                f"Full PM3 requires confirming partner variant origin."
            )
            return strength, notes
        elif p1_het and p2_het:
            notes.append(
                f"PM3 not assigned: both parents carry this variant "
                f"(P1={parent1_gt}, P2={parent2_gt}) — trans configuration not confirmed."
            )
            return None, notes
        else:
            notes.append(
                f"PM3 not assigned: parental genotypes ({parent1_gt}, {parent2_gt}) "
                f"do not support trans configuration."
            )
            return None, notes

    # --- Solo mode: rely on WhatsHap phasing ---
    if phase_status == PHASE_TRANS:
        if phase_confidence == "HIGH":
            strength = "Moderate"
            notes.append(
                f"PM3 (Moderate): WhatsHap phasing (HIGH confidence) places variant "
                f"in trans with partner {phase_partner or 'unknown'}. "
                f"No parental VCFs available to confirm — flagged as limitation."
            )
        elif phase_confidence == "MEDIUM":
            strength = "Supporting"
            notes.append(
                f"PM3 (Supporting): WhatsHap phasing (MEDIUM confidence) suggests trans "
                f"configuration with {phase_partner or 'unknown'}. "
                f"Reduced strength due to phasing uncertainty and no parental confirmation."
            )
        else:
            notes.append(
                f"PM3 not assigned: trans phasing detected but LOW confidence. "
                f"Parental VCFs recommended to confirm."
            )
            return None, notes
        return strength, notes

    # Unphased or N/A
    notes.append(
        "PM3 not evaluable: variant unphased or no compound het partner identified. "
        "Provide parental VCFs (trio mode) or read-backed phasing data for PM3 evaluation."
    )
    return None, notes


# ---------------------------------------------------------------------------
# BP2 evaluation
# ---------------------------------------------------------------------------

def _evaluate_bp2(
    phase_status: str,
    phase_confidence: str,
    phase_partner: Optional[str],
    inheritance: Optional[str],
    trio_mode: bool,
    parent1_gt: Optional[str],
    parent2_gt: Optional[str],
) -> tuple[Optional[str], list[str]]:
    """
    BP2: Observed in cis with a pathogenic variant (recessive) OR
         in trans with a pathogenic variant in a dominant gene.

    Returns (strength, notes).
    """
    notes = []

    is_dominant  = inheritance and "AD" in inheritance.upper()
    is_recessive = inheritance and "AR" in inheritance.upper()

    # --- Trio mode ---
    if trio_mode and parent1_gt and parent2_gt:
        p1_het = parent1_gt in {"0/1", "0|1", "1|0"}
        p2_het = parent2_gt in {"0/1", "0|1", "1|0"}

        if p1_het and p2_het:
            # Both parents carry it — inherited, less likely de novo pathogenic
            # For dominant: variant in trans with other parent's known P variant → BP2
            notes.append(
                f"BP2 signal: variant present in both parents (P1={parent1_gt}, "
                f"P2={parent2_gt}). For dominant inheritance, in trans with any "
                f"pathogenic variant in other allele reduces pathogenicity. "
                f"LLM will evaluate full BP2 applicability."
            )
            return "Supporting", notes

    # --- Solo mode: CIS phasing ---
    if phase_status == PHASE_CIS and phase_confidence in {"HIGH", "MEDIUM"}:
        notes.append(
            f"BP2 (Supporting): WhatsHap phasing ({phase_confidence} confidence) places "
            f"variant in CIS with partner {phase_partner or 'unknown'}. "
            f"Cis with a pathogenic variant in a recessive gene = benign for this allele."
        )
        return "Supporting", notes

    notes.append(
        "BP2 not evaluable from available data. "
        "Requires phasing data or parental VCFs to determine cis/trans configuration."
    )
    return None, notes


# ---------------------------------------------------------------------------
# PP1 evaluation
# ---------------------------------------------------------------------------

def _evaluate_pp1(
    trio_mode: bool,
    parent1_gt: Optional[str],
    parent2_gt: Optional[str],
    denovo_status: Optional[str],
    inheritance: Optional[str],
) -> tuple[Optional[str], list[str]]:
    """
    PP1: Co-segregation with disease in affected family members.

    Solo: not evaluable — flag limitation.
    Trio: can only provide weak proxy (variant inherited from affected parent).
    Full PP1 requires multi-generation pedigree data.
    """
    notes = []

    if not trio_mode:
        notes.append(
            "PP1 not evaluable in solo mode. Co-segregation analysis requires "
            "family member genotypes. Provide parental VCFs for partial evaluation, "
            "or full pedigree data for PP1 Moderate/Strong."
        )
        return None, notes

    # Trio: if variant is de novo, PP1 doesn't apply (no inheritance to segregate)
    if denovo_status == "confirmed":
        notes.append(
            "PP1 not applicable: variant confirmed de novo — no segregation pattern."
        )
        return None, notes

    # Trio: if inherited from a parent, very weak PP1 signal (only 1 meiosis)
    p1_het = parent1_gt and parent1_gt in {"0/1", "0|1", "1|0"}
    p2_het = parent2_gt and parent2_gt in {"0/1", "0|1", "1|0"}

    if p1_het or p2_het:
        notes.append(
            f"PP1 (Supporting, limited): Variant inherited from a parent "
            f"(P1={parent1_gt}, P2={parent2_gt}). This provides 1 informative meiosis — "
            f"insufficient for PP1 Moderate/Strong. Limitation: parental phenotype unknown. "
            f"Full segregation study recommended."
        )
        return "Supporting", notes

    notes.append(
        "PP1 not assignable from trio data alone. "
        "Parental phenotype status required to evaluate co-segregation."
    )
    return None, notes


# ---------------------------------------------------------------------------
# BS4 evaluation
# ---------------------------------------------------------------------------

def _evaluate_bs4(
    trio_mode: bool,
    parent1_gt: Optional[str],
    parent2_gt: Optional[str],
    denovo_status: Optional[str],
    inheritance: Optional[str],
) -> tuple[Optional[str], list[str]]:
    """
    BS4: Lack of segregation in affected family members.

    Solo: not evaluable.
    Trio: if variant absent in an affected parent → weak BS4 signal.
    """
    notes = []

    if not trio_mode:
        notes.append(
            "BS4 not evaluable in solo mode. "
            "Requires family segregation data."
        )
        return None, notes

    p1_ref = parent1_gt and parent1_gt in {"0/0", "0|0"}
    p2_ref = parent2_gt and parent2_gt in {"0/0", "0|0"}

    if p1_ref or p2_ref:
        notes.append(
            f"BS4 signal: variant absent in at least one parent "
            f"(P1={parent1_gt}, P2={parent2_gt}). "
            f"If the absent parent is affected, this is evidence against pathogenicity. "
            f"Limitation: parental phenotype unknown — cannot confirm BS4 without "
            f"clinical records. Parental phenotype data required for full BS4."
        )
        # Return Supporting only — can't confirm without parental phenotype
        return "Supporting", notes

    notes.append("BS4 not triggered: no parental reference genotype found.")
    return None, notes


# ---------------------------------------------------------------------------
# LLM refinement
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ACMG/AMP variant classification expert evaluating segregation
and phase evidence. You assess PP1, PM3, BP2, and BS4.

Key rules:
- PP1 (Supporting/Moderate/Strong): Co-segregation requires affected family members with
  the variant. 1 meiosis = Supporting only. LOD ≥1.0 = Moderate. LOD ≥2.0 = Strong.
  Cannot assign PP1 without phenotype-confirmed family members.
- PM3 (Moderate): Trans with pathogenic variant in AR gene. Requires confirmed phase.
  Downgrade to Supporting if phase confidence is MEDIUM or parental genotypes not available.
- BP2 (Supporting): Cis with pathogenic in AR, or trans with pathogenic in AD.
- BS4 (Strong): Non-segregation in affected members. Cannot assign without affected
  family members confirmed clinically. In trio mode without phenotype = Supporting only.
- Always flag limitations when parental phenotype data is absent.

Respond ONLY with a JSON object. No preamble, no markdown fences. Schema:
{
  "criteria_pathogenic": {},
  "criteria_benign": {},
  "evidence_notes": "string — 3-5 sentences including limitations",
  "citations": ["sources"],
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "limitations": ["list of specific limitations due to missing data"]
}"""


def _llm_refine(
    state: VariantState,
    rule_criteria_p: dict,
    rule_criteria_b: dict,
    all_notes: list[str],
) -> dict:
    gene        = state.get("gene", "UNKNOWN")
    variant_id  = state.get("variant_id", "?")
    consequence = state.get("consequence", "")
    inheritance = state.get("gene_orphanet_inheritance") or "Unknown"
    clingen     = state.get("gene_clingen_validity") or "Unknown"
    phase_status = state.get("phase_status", "not_applicable")
    phase_conf  = state.get("phase_confidence", "LOW")
    phase_partner = state.get("phase_partner") or "None"
    trio_mode   = state.get("trio_mode", False)
    p1_gt       = state.get("parent1_genotype") or "N/A"
    p2_gt       = state.get("parent2_genotype") or "N/A"
    denovo      = state.get("denovo_status") or "unknown"

    user_prompt = f"""Evaluate segregation and phase evidence for this variant:

Gene: {gene} | Variant: {variant_id}
Consequence: {consequence}
Inheritance (Orphanet): {inheritance}
ClinGen validity: {clingen}

Phase information (from WhatsHap):
  Phase status: {phase_status}
  Phase confidence: {phase_conf}
  Phase partner variant: {phase_partner}

Trio mode: {trio_mode}
  Parent 1 genotype at this locus: {p1_gt}
  Parent 2 genotype at this locus: {p2_gt}
  De novo status: {denovo}

Rule-based pre-evaluation:
  Pathogenic criteria: {rule_criteria_p}
  Benign criteria: {rule_criteria_b}
  Notes: {'; '.join(all_notes)}

Evaluate PP1, PM3, BP2, BS4 given the available data.
Be explicit about what cannot be determined due to missing parental phenotype data.
In trio mode without phenotype confirmation, use Supporting strength only for
PP1 and BS4."""

    return call_llm_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def agent6_segregation(state: VariantState) -> dict:
    """
    Agent 6: Evaluate segregation and phase criteria (PP1, PM3, BP2, BS4).

    Operates in solo or trio mode depending on state.trio_mode.
    Flags all limitations due to missing parental phenotype data.

    Returns:
        dict with key "agent_evidence" -> {"agent6": AgentEvidence dict}
    """
    gene        = state.get("gene", "UNKNOWN")
    variant_id  = state.get("variant_id", "?")
    trio_mode   = state.get("trio_mode", False)
    logger.info(
        f"[agent6_segregation] Evaluating {variant_id} ({gene}) "
        f"mode={'trio' if trio_mode else 'solo'}"
    )

    phase_status  = state.get("phase_status", PHASE_NA) or PHASE_NA
    phase_conf    = state.get("phase_confidence", "LOW") or "LOW"
    phase_partner = state.get("phase_partner")
    inheritance   = state.get("gene_orphanet_inheritance") or ""
    p1_gt         = state.get("parent1_genotype")
    p2_gt         = state.get("parent2_genotype")
    denovo_status = state.get("denovo_status")

    criteria_p: dict = {}
    criteria_b: dict = {}
    all_notes:  list[str] = []
    limitations: list[str] = []
    citations = ["ACMG/AMP 2015", "ClinGen SVI segregation guidance"]

    # --- PP1 ---
    pp1_strength, pp1_notes = _evaluate_pp1(
        trio_mode, p1_gt, p2_gt, denovo_status, inheritance
    )
    if pp1_strength:
        criteria_p["PP1"] = pp1_strength
    all_notes.extend(pp1_notes)
    if not trio_mode:
        limitations.append("PP1/BS4: solo mode — no parental genotypes available")

    # --- PM3 ---
    pm3_strength, pm3_notes = _evaluate_pm3(
        phase_status, phase_conf, phase_partner,
        inheritance, trio_mode, p1_gt, p2_gt,
    )
    if pm3_strength:
        criteria_p["PM3"] = pm3_strength
    all_notes.extend(pm3_notes)

    # --- BP2 ---
    bp2_strength, bp2_notes = _evaluate_bp2(
        phase_status, phase_conf, phase_partner,
        inheritance, trio_mode, p1_gt, p2_gt,
    )
    if bp2_strength:
        criteria_b["BP2"] = bp2_strength
    all_notes.extend(bp2_notes)

    # --- BS4 ---
    bs4_strength, bs4_notes = _evaluate_bs4(
        trio_mode, p1_gt, p2_gt, denovo_status, inheritance
    )
    if bs4_strength:
        criteria_b["BS4"] = bs4_strength
    all_notes.extend(bs4_notes)

    # --- LLM refinement ---
    # Call LLM when: any criteria assigned (to confirm), or trio mode with data
    needs_llm = (
        bool(criteria_p or criteria_b) or
        (trio_mode and (p1_gt or p2_gt)) or
        phase_status == PHASE_TRANS
    )

    if needs_llm:
        logger.debug(f"[agent6] Calling LLM for {variant_id}")
        llm_result = _llm_refine(state, criteria_p, criteria_b, all_notes)

        if llm_result and not llm_result.get("error"):
            criteria_p     = llm_result.get("criteria_pathogenic", criteria_p)
            criteria_b     = llm_result.get("criteria_benign", criteria_b)
            confidence     = llm_result.get("confidence", "MEDIUM")
            evidence_notes = llm_result.get("evidence_notes", " ".join(all_notes))
            citations     += llm_result.get("citations", [])
            limitations   += llm_result.get("limitations", [])
        else:
            logger.warning(f"[agent6] LLM failed — rule-based only")
            confidence = "LOW" if not trio_mode else "MEDIUM"
            evidence_notes = " ".join(all_notes)
    else:
        # Solo mode, no phase data, no trio data — nothing to evaluate
        confidence = "LOW"
        evidence_notes = (
            f"Segregation criteria (PP1, PM3, BP2, BS4) not evaluable for {gene} "
            f"in solo mode without phasing data. "
            f"Provide parental VCFs or family genotype data to enable this agent."
        )

    # Always append limitations to notes
    if limitations:
        evidence_notes += " LIMITATIONS: " + "; ".join(limitations)

    citations = list(dict.fromkeys(citations))
    logger.info(
        f"[agent6] {variant_id}: P={criteria_p} B={criteria_b} "
        f"conf={confidence} mode={'trio' if trio_mode else 'solo'}"
    )

    return {
        "agent_evidence": {
            "agent6": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign":     criteria_b,
                "evidence_notes":      evidence_notes,
                "citations":           citations,
                "confidence":          confidence,
            }
        }
    }

