"""
src/agents/agent9_phenotype.py
Agent 9 — Phenotype Evidence
Evaluates: PP4, BP5

ACMG/AMP 2015 criteria:
  PP4  — Patient's phenotype or family history is highly specific for a disease
         with a single genetic etiology.
         Pathogenic Supporting.
         Fires when: patient HPO terms match the gene's disease phenotype
         with high specificity (few genes cause this phenotype combination).

  BP5  — Variant found in a case with an alternate molecular basis for disease.
         Benign Supporting.
         Fires when: another causative variant has already been identified
         in the same patient (alternate_molecular_diagnosis set in state).

Logic:
  PP4 uses Orphanet gene-disease associations + patient HPO terms.
  Both are rule-based with LLM refinement for PP4 specificity scoring.
  BP5 is purely rule-based (no LLM needed).

State fields read:
  gene, variant_id, consequence,
  patient_hpo_terms,           # list[str] — HPO IDs e.g. ["HP:0001250", ...]
  hpo_matched_genes,           # list[str] — genes matching patient HPO (set by hpo_matcher)
  gene_orphanet_inheritance,   # str — inheritance pattern
  gene_orphanet_diseases,      # list[str] — disease names for this gene
  phenotype_score,             # float — 0.0-1.0 from phenotype_scorer node (if run)
  alternate_molecular_diagnosis, # str|None — if another cause already found
  gene_clingen_validity,       # str — ClinGen disease validity

State fields written:
  agent_evidence["agent9"]
"""

import logging
from src.utils.logging_config import get_user_friendly_logger
from typing import Optional
from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json

logger = get_user_friendly_logger('agent9_phenotype')

# Thresholds
PP4_PHENOTYPE_SCORE_THRESHOLD = 0.6   # phenotype_score >= this → PP4 candidate
PP4_HPO_MATCH_MIN             = 2     # minimum matching HPO terms to consider PP4
PP4_SPECIFICITY_MAX_GENES     = 5     # if fewer than this many genes cause phenotype → specific


# ---------------------------------------------------------------------------
# BP5 — Alternate molecular diagnosis
# ---------------------------------------------------------------------------

def _evaluate_bp5(
    alternate_molecular_diagnosis: Optional[str],
    variant_id: str,
) -> tuple[Optional[str], list[str]]:
    notes = []
    if not alternate_molecular_diagnosis:
        notes.append(
            "BP5 not triggered: no alternate molecular diagnosis recorded in state."
        )
        return None, notes
    notes.append(
        f"BP5 (Supporting): An alternate molecular basis for disease has been "
        f"identified in this patient ({alternate_molecular_diagnosis}). "
        f"This variant is less likely to be the primary cause."
    )
    return "Supporting", notes


# ---------------------------------------------------------------------------
# PP4 — Phenotype specificity
# ---------------------------------------------------------------------------

def _evaluate_pp4(
    gene: str,
    patient_hpo_terms: list,
    hpo_matched_genes: list,
    gene_orphanet_diseases: list,
    phenotype_score: Optional[float],
    clingen_validity: Optional[str],
) -> tuple[Optional[str], list[str]]:
    notes = []

    # No HPO terms — cannot evaluate
    if not patient_hpo_terms:
        notes.append(
            "PP4 not evaluable: no patient HPO terms available. "
            "Provide HPO terms or clinical notes for phenotype matching."
        )
        return None, notes

    # No disease association for this gene
    if not gene_orphanet_diseases:
        notes.append(
            f"PP4 not evaluable: no Orphanet disease associations found for {gene}."
        )
        return None, notes

    # ClinGen validity check — PP4 is stronger for genes with established validity
    validity_ok = clingen_validity and clingen_validity.lower() not in (
        "no reported evidence", "disputed", "refuted"
    )
    if not validity_ok:
        notes.append(
            f"PP4 weakened: ClinGen validity for {gene} is '{clingen_validity}'. "
            f"PP4 requires established gene-disease relationship."
        )

    # phenotype_score set by phenotype_scorer node (HPO overlap scoring)
    if phenotype_score is not None:
        if phenotype_score >= PP4_PHENOTYPE_SCORE_THRESHOLD:
            # Check specificity: how many genes match this phenotype?
            n_matched = len(hpo_matched_genes) if hpo_matched_genes else 999
            if n_matched <= PP4_SPECIFICITY_MAX_GENES:
                notes.append(
                    f"PP4 (Supporting): Phenotype score={phenotype_score:.2f} ≥ "
                    f"{PP4_PHENOTYPE_SCORE_THRESHOLD} for {gene}. "
                    f"Only {n_matched} gene(s) match this HPO profile — "
                    f"high phenotype specificity. "
                    f"Associated diseases: {', '.join(gene_orphanet_diseases[:3])}."
                )
                return "Supporting", notes
            else:
                notes.append(
                    f"PP4 not assigned: phenotype score={phenotype_score:.2f} is above "
                    f"threshold but {n_matched} genes match this HPO profile — "
                    f"insufficient specificity for PP4 (need ≤{PP4_SPECIFICITY_MAX_GENES} genes)."
                )
                return None, notes
        else:
            notes.append(
                f"PP4 not assigned: phenotype score={phenotype_score:.2f} below "
                f"threshold {PP4_PHENOTYPE_SCORE_THRESHOLD} for {gene}."
            )
            return None, notes

    # phenotype_scorer not run yet — fall back to HPO term count heuristic
    n_hpo = len([t for t in patient_hpo_terms if t.get("present", True)])
    n_matched_genes = len(hpo_matched_genes) if hpo_matched_genes else 999

    if n_hpo >= PP4_HPO_MATCH_MIN and gene in (hpo_matched_genes or []):
        if n_matched_genes <= PP4_SPECIFICITY_MAX_GENES:
            notes.append(
                f"PP4 (Supporting, heuristic): {gene} is among {n_matched_genes} "
                f"gene(s) matching patient's {n_hpo} HPO terms. "
                f"High specificity. No phenotype_score available — "
                f"LLM will assess full PP4 applicability."
            )
            return "Supporting", notes
        else:
            notes.append(
                f"PP4 not assigned (heuristic): {gene} matches HPO terms but "
                f"{n_matched_genes} genes match — not specific enough."
            )
            return None, notes

    notes.append(
        f"PP4 not assigned: {gene} not in HPO-matched gene list or "
        f"insufficient HPO overlap ({n_hpo} terms, matched genes: {n_matched_genes})."
    )
    return None, notes


# ---------------------------------------------------------------------------
# LLM refinement (PP4 only — BP5 is purely rule-based)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ACMG/AMP variant classification expert evaluating phenotype evidence.
You assess PP4 only (BP5 is rule-based and not subject to LLM review).

Key rules:
- PP4 (Supporting): Patient phenotype is HIGHLY SPECIFIC for a single-gene disorder
  AND the variant's gene is known to cause that disorder.
  Requires: (1) established gene-disease relationship, (2) phenotype not explained
  by other common genes, (3) patient HPO terms match the known disease phenotype.
- PP4 cannot be assigned if: ClinGen validity is Disputed/Refuted, or the phenotype
  is non-specific (e.g., intellectual disability caused by hundreds of genes).
- PP4 can be upgraded to Moderate by some ClinGen gene-specific guidelines.
- Do NOT assign PP4 if phenotype_score is below 0.6 or HPO matching is weak.

Respond ONLY with a JSON object. No preamble, no markdown fences. Schema:
{
  "criteria_pathogenic": {},
  "criteria_benign": {},
  "evidence_notes": "string — 3-5 sentences",
  "citations": ["sources"],
  "confidence": "HIGH" | "MEDIUM" | "LOW"
}"""


def _llm_refine(
    state: VariantState,
    criteria_p: dict,
    criteria_b: dict,
    all_notes: list[str],
) -> dict:
    gene              = state.get("gene", "UNKNOWN")
    variant_id        = state.get("variant_id", "?")
    hpo_terms         = state.get("patient_hpo_terms") or []
    hpo_matched_genes = state.get("hpo_matched_genes") or []
    orphanet_diseases = state.get("gene_orphanet_diseases") or []
    phenotype_score   = state.get("phenotype_score")
    clingen_validity  = state.get("gene_clingen_validity") or "Unknown"
    inheritance       = state.get("gene_orphanet_inheritance") or "Unknown"
    alt_dx            = state.get("alternate_molecular_diagnosis") or "None"

    user_prompt = f"""Evaluate phenotype evidence for this variant:

Gene: {gene} | Variant: {variant_id}
ClinGen validity: {clingen_validity}
Orphanet inheritance: {inheritance}
Orphanet diseases for {gene}: {', '.join(orphanet_diseases[:5]) or 'None'}

Patient HPO terms: {', '.join(t.get("hpo_id","?") + " " + t.get("label","") for t in hpo_terms[:20]) or 'None'}
Genes matching patient HPO: {', '.join(hpo_matched_genes[:10]) or 'None'} ({len(hpo_matched_genes)} total)
Phenotype score: {phenotype_score if phenotype_score is not None else 'not computed'}
Alternate molecular diagnosis: {alt_dx}

Rule-based pre-evaluation:
  Pathogenic criteria: {criteria_p}
  Benign criteria: {criteria_b}
  Notes: {'; '.join(all_notes)}

Evaluate PP4 only. Confirm or correct the rule-based PP4 assignment.
Consider whether the phenotype is specific enough for a single-gene disorder.
Note if the HPO terms are hallmark features of the gene's disease."""

    return call_llm_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def agent9_phenotype(state: VariantState) -> dict:
    """
    Agent 9: Evaluate phenotype criteria (PP4, BP5).
    PP4 requires patient HPO terms and Orphanet gene-disease data.
    BP5 requires alternate_molecular_diagnosis to be set in state.

    Returns:
        dict with key "agent_evidence" -> {"agent9": AgentEvidence dict}
    """
    gene       = state.get("gene", "UNKNOWN")
    variant_id = state.get("variant_id", "?")
    logger.info(f" Evaluating {variant_id} ({gene})")

    patient_hpo_terms  = state.get("patient_hpo_terms") or []
    hpo_matched_genes  = state.get("hpo_matched_genes") or []
    orphanet_diseases  = state.get("gene_orphanet_diseases") or []
    phenotype_score    = state.get("phenotype_score")
    clingen_validity   = state.get("gene_clingen_validity") or ""
    alt_dx             = state.get("alternate_molecular_diagnosis")

    criteria_p: dict = {}
    criteria_b: dict = {}
    all_notes:  list[str] = []
    citations = ["ACMG/AMP 2015", "Orphanet"]

    # --- BP5 (rule-based, no LLM) ---
    bp5_strength, bp5_notes = _evaluate_bp5(alt_dx, variant_id)
    if bp5_strength:
        criteria_b["BP5"] = bp5_strength
    all_notes.extend(bp5_notes)

    # --- PP4 ---
    pp4_strength, pp4_notes = _evaluate_pp4(
        gene, patient_hpo_terms, hpo_matched_genes,
        orphanet_diseases, phenotype_score, clingen_validity,
    )
    if pp4_strength:
        criteria_p["PP4"] = pp4_strength
    all_notes.extend(pp4_notes)

    # --- LLM refinement for PP4 ---
    needs_llm = bool(patient_hpo_terms) and bool(orphanet_diseases)

    if needs_llm:
        logger.debug(f" Calling LLM for {variant_id}")
        llm_result = _llm_refine(state, criteria_p, criteria_b, all_notes)
        if llm_result and not llm_result.get("error"):
            criteria_p     = llm_result.get("criteria_pathogenic", criteria_p)
            criteria_b     = llm_result.get("criteria_benign", criteria_b)
            confidence     = llm_result.get("confidence", "MEDIUM")
            evidence_notes = llm_result.get("evidence_notes", " ".join(all_notes))
            citations     += llm_result.get("citations", [])
        else:
            logger.warning(f" LLM failed — rule-based only")
            confidence     = "MEDIUM" if (criteria_p or criteria_b) else "LOW"
            evidence_notes = " ".join(all_notes)
    else:
        confidence     = "LOW" if not patient_hpo_terms else "MEDIUM"
        evidence_notes = " ".join(all_notes) if all_notes else (
            f"Phenotype criteria not evaluable for {gene}: "
            f"no HPO terms or Orphanet disease data available."
        )

    citations = list(dict.fromkeys(citations))

    logger.info(
        f"[agent9] {variant_id}: P={criteria_p} B={criteria_b} conf={confidence}"
    )

    return {
        "agent_evidence": {
            "agent9": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign":     criteria_b,
                "evidence_notes":      evidence_notes,
                "citations":           citations,
                "confidence":          confidence,
            }
        }
    }
