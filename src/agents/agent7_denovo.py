"""
src/agents/agent7_denovo.py
Agent 7 — De Novo Evidence
Evaluates: PS2, PM6

ACMG/AMP 2015 criteria:
  PS2  — De novo (both maternity and paternity confirmed) in a patient with disease
         and no family history. Pathogenic Strong.
         Requires: variant absent in BOTH parents AND parental identity confirmed.
         SOLO: not assignable — cannot confirm absence in parents.
         TRIO: assignable if both parents are 0/0 AND denovo_status = "confirmed".
         → Downgrade to PM6 if maternity/paternity NOT confirmed.

  PM6  — Assumed de novo (parental testing NOT done or identity unconfirmed).
         Pathogenic Moderate.
         SOLO: not reliably assignable — flag as not evaluable.
         TRIO: assigned if both parents 0/0 but identity confirmation absent.
         → Used when trio data shows absence in parents but we lack
           formal confirmation of sample identity.

Solo mode: Agent returns empty criteria + LOW confidence + limitation note.
This is correct behavior per the handoff spec — do not attempt to infer
de novo status from allele frequency or other proxies here (agent1 handles AF).

State fields read:
  trio_mode, parent1_genotype, parent2_genotype, denovo_status,
  gene, variant_id, consequence,
  gene_clingen_validity, gene_orphanet_inheritance,
  clinvar_classification, clinvar_review_stars

State fields written (via agent_evidence):
  agent_evidence["agent7"]
"""

import logging
from src.utils.logging_config import get_user_friendly_logger
from typing import Optional
from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json

logger = get_user_friendly_logger('agent7_denovo')


# ---------------------------------------------------------------------------
# PS2 / PM6 evaluation
# ---------------------------------------------------------------------------

def _evaluate_denovo(
    trio_mode: bool,
    parent1_gt: Optional[str],
    parent2_gt: Optional[str],
    denovo_status: Optional[str],
    inheritance: Optional[str],
    clingen_validity: Optional[str],
) -> tuple[dict, dict, list[str], list[str]]:
    """
    Core rule-based evaluation for PS2 and PM6.

    Returns:
        criteria_p, criteria_b, notes, limitations
    """
    criteria_p: dict = {}
    criteria_b: dict = {}
    notes: list[str] = []
    limitations: list[str] = []

    # --- Solo mode: nothing evaluable ---
    if not trio_mode:
        notes.append(
            "PS2/PM6 not evaluable in solo mode. "
            "De novo assessment requires parental genotypes. "
            "Provide parental VCFs to enable this agent."
        )
        limitations.append(
            "PS2/PM6: solo mode — parental genotypes unavailable"
        )
        return criteria_p, criteria_b, notes, limitations

    # --- Trio mode: check parental genotypes ---
    if not parent1_gt or not parent2_gt:
        notes.append(
            "Trio mode active but one or both parental genotypes missing at this locus. "
            "Cannot confirm de novo status. Check parental VCF coverage at this position."
        )
        limitations.append(
            "Parental genotype(s) missing at variant locus despite trio mode"
        )
        return criteria_p, criteria_b, notes, limitations

    p1_ref = parent1_gt in {"0/0", "0|0"}
    p2_ref = parent2_gt in {"0/0", "0|0"}
    p1_het = parent1_gt in {"0/1", "0|1", "1|0"}
    p2_het = parent2_gt in {"0/1", "0|1", "1|0"}

    # Variant present in a parent → de novo excluded
    if p1_het or p2_het:
        notes.append(
            f"De novo excluded: variant present in at least one parent "
            f"(P1={parent1_gt}, P2={parent2_gt}). PS2/PM6 not applicable."
        )
        return criteria_p, criteria_b, notes, limitations

    # Variant homozygous/alt in a parent → also not de novo
    if not (p1_ref and p2_ref):
        notes.append(
            f"Parental genotypes unexpected (P1={parent1_gt}, P2={parent2_gt}). "
            f"Cannot confirm de novo. Manual review required."
        )
        limitations.append("Unexpected parental genotype — manual review required")
        return criteria_p, criteria_b, notes, limitations

    # Both parents are 0/0 — candidate de novo
    # Now distinguish PS2 vs PM6 based on denovo_status field
    # denovo_status is set by the phasing/annotation layer:
    #   "confirmed"  — parental identity confirmed (e.g. STR/SNP fingerprint)
    #   "possible"   — parents are ref/ref but identity not formally confirmed
    #   "excluded"   — variant found in a parent
    #   "unknown"    — not assessed

    if denovo_status == "confirmed":
        # PS2: de novo with confirmed parentage
        strength = "Strong"
        criteria_p["PS2"] = strength
        notes.append(
            f"PS2 (Strong): Both parents homozygous reference at this locus "
            f"(P1={parent1_gt}, P2={parent2_gt}) with confirmed parental identity. "
            f"De novo variant in gene with established disease association ({clingen_validity or 'unknown ClinGen validity'})."
        )
        # Inheritance check: PS2 is most meaningful for dominant/de novo genes
        if inheritance and "AR" in inheritance.upper():
            notes.append(
                "Note: PS2 in AR gene — de novo in a recessive gene is less typical "
                "but valid if gene has known de novo pathogenic variants."
            )

    elif denovo_status in {"possible", "unknown", None}:
        # PM6: assumed de novo — parentage not confirmed
        strength = "Moderate"
        criteria_p["PM6"] = strength
        notes.append(
            f"PM6 (Moderate): Both parents ref/ref at this locus "
            f"(P1={parent1_gt}, P2={parent2_gt}) but parental identity not confirmed. "
            f"Assigned PM6 (assumed de novo) rather than PS2. "
            f"Formal parental confirmation (STR/SNP panel) would upgrade to PS2."
        )
        limitations.append(
            "PM6 assigned instead of PS2: parental identity confirmation absent. "
            "Upgrade to PS2 if sample identity verified."
        )

    elif denovo_status == "excluded":
        notes.append(
            "De novo status explicitly excluded by phasing/annotation layer. "
            "PS2/PM6 not assigned."
        )

    return criteria_p, criteria_b, notes, limitations


# ---------------------------------------------------------------------------
# LLM refinement
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ACMG/AMP variant classification expert evaluating de novo evidence.
You assess PS2 and PM6 only.

Key rules:
- PS2 (Strong): De novo variant with BOTH maternity AND paternity confirmed.
  Requires: variant absent in both parents AND sample identity formally confirmed.
  Cannot assign PS2 without explicit identity confirmation.
- PM6 (Moderate): Assumed de novo — parents show ref/ref but identity not confirmed.
  Appropriate when parental genotypes support de novo but formal confirmation lacking.
- Neither criterion is assignable in solo mode (no parental genotypes).
- PS2/PM6 strength can be upgraded to Very Strong by ClinGen for specific genes
  (e.g. KCNQ2, SCN1A) — note if applicable.
- For AR genes, de novo is uncommon but not impossible; note the discordance.
- Always flag if parental phenotype is unknown (affects disease context for PS2).

Respond ONLY with a JSON object. No preamble, no markdown fences. Schema:
{
  "criteria_pathogenic": {},
  "criteria_benign": {},
  "evidence_notes": "string — 3-5 sentences",
  "citations": ["sources"],
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "limitations": ["list of limitations"]
}"""


def _llm_refine(
    state: VariantState,
    criteria_p: dict,
    criteria_b: dict,
    notes: list[str],
    limitations: list[str],
) -> dict:
    gene         = state.get("gene", "UNKNOWN")
    variant_id   = state.get("variant_id", "?")
    consequence  = state.get("consequence", "")
    inheritance  = state.get("gene_orphanet_inheritance") or "Unknown"
    clingen      = state.get("gene_clingen_validity") or "Unknown"
    p1_gt        = state.get("parent1_genotype") or "N/A"
    p2_gt        = state.get("parent2_genotype") or "N/A"
    denovo       = state.get("denovo_status") or "unknown"
    clinvar_sig  = state.get("clinvar_classification") or "unknown"

    user_prompt = f"""Evaluate de novo evidence for this variant:

Gene: {gene} | Variant: {variant_id}
Consequence: {consequence}
Inheritance (Orphanet): {inheritance}
ClinGen validity: {clingen}
ClinVar significance: {clinvar_sig}

Trio genotypes:
  Parent 1 at this locus: {p1_gt}
  Parent 2 at this locus: {p2_gt}
  De novo status (from pipeline): {denovo}

Rule-based pre-evaluation:
  Pathogenic criteria: {criteria_p}
  Benign criteria: {criteria_b}
  Notes: {'; '.join(notes)}
  Limitations: {'; '.join(limitations)}

Evaluate PS2 and PM6 given the above.
If denovo_status = "confirmed" and both parents are ref/ref, confirm PS2.
If parents are ref/ref but identity unconfirmed, confirm PM6.
Check if this gene has ClinGen-approved PS2 upgrades (e.g. KCNQ2, SCN1A, FOXG1).
Be explicit about parental phenotype being unknown."""

    return call_llm_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def agent7_denovo(state: VariantState) -> dict:
    """
    Agent 7: Evaluate de novo criteria (PS2, PM6).

    Solo mode: returns empty criteria with LOW confidence and limitation note.
    Trio mode: evaluates based on parental genotypes and denovo_status.

    Returns:
        dict with key "agent_evidence" -> {"agent7": AgentEvidence dict}
    """
    gene       = state.get("gene", "UNKNOWN")
    variant_id = state.get("variant_id", "?")
    trio_mode  = state.get("trio_mode", False)

    logger.info(
        f"[agent7_denovo] Evaluating {variant_id} ({gene}) "
        f"mode={'trio' if trio_mode else 'solo'}"
    )

    p1_gt        = state.get("parent1_genotype")
    p2_gt        = state.get("parent2_genotype")
    denovo_status = state.get("denovo_status")
    inheritance  = state.get("gene_orphanet_inheritance") or ""
    clingen      = state.get("gene_clingen_validity") or ""

    # Rule-based evaluation
    criteria_p, criteria_b, notes, limitations = _evaluate_denovo(
        trio_mode, p1_gt, p2_gt, denovo_status, inheritance, clingen
    )

    citations = ["ACMG/AMP 2015", "ClinGen PS2/PM6 guidance"]

    # LLM refinement: only when trio mode has actual data to refine
    needs_llm = trio_mode and (criteria_p or criteria_b)

    if needs_llm:
        logger.debug(f" Calling LLM for {variant_id}")
        llm_result = _llm_refine(state, criteria_p, criteria_b, notes, limitations)

        if llm_result and not llm_result.get("error"):
            criteria_p     = llm_result.get("criteria_pathogenic", criteria_p)
            criteria_b     = llm_result.get("criteria_benign", criteria_b)
            confidence     = llm_result.get("confidence", "MEDIUM")
            evidence_notes = llm_result.get("evidence_notes", " ".join(notes))
            citations     += llm_result.get("citations", [])
            limitations   += llm_result.get("limitations", [])
        else:
            logger.warning(f" LLM failed — rule-based only")
            confidence     = "MEDIUM"
            evidence_notes = " ".join(notes)
    else:
        # Solo mode or no criteria triggered
        confidence     = "LOW"
        evidence_notes = " ".join(notes) if notes else (
            f"De novo criteria (PS2, PM6) not evaluable for {gene} "
            f"({'solo mode — no parental genotypes' if not trio_mode else 'trio mode but no criteria triggered'})."
        )

    if limitations:
        evidence_notes += " LIMITATIONS: " + "; ".join(limitations)

    citations = list(dict.fromkeys(citations))

    logger.info(
        f"[agent7] {variant_id}: P={criteria_p} B={criteria_b} "
        f"conf={confidence} mode={'trio' if trio_mode else 'solo'}"
    )

    return {
        "agent_evidence": {
            "agent7": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign":     criteria_b,
                "evidence_notes":      evidence_notes,
                "citations":           citations,
                "confidence":          confidence,
            }
        }
    }
