"""
src/agents/agent8_gene_context.py
Agent 8 — Gene Context Evidence
Evaluates: PM4, PM5, PP2, BP1, BP3

ACMG/AMP 2015 criteria:
  PM4  — Protein length change (in-frame indel or stop-loss in non-repeat region).
         Pathogenic Moderate.
         Rule-based: consequence = inframe_insertion/deletion/stop_lost
         + NOT in repeat region (RepeatMasker flag from VEP).

  PM5  — Novel missense at same amino acid position as known pathogenic missense.
         Pathogenic Moderate.
         RAG: query clinvar_gene_variants for same codon, different AA change.

  PP2  — Missense variant in gene with low rate of benign missense variation
         AND where missense is a common mechanism.
         Pathogenic Supporting.
         Rule-based: missense + gene has ClinGen/gnomAD missense constraint
         (Z-score or oe_mis from gnomAD pLI metrics file).

  BP1  — Missense variant in gene where only truncating variants cause disease.
         Benign Supporting.
         Rule-based: missense + ClinGen disease mechanism = LOF only.

  BP3  — In-frame indel in repeat region without known function.
         Benign Supporting.
         Rule-based: inframe indel + repeat region flag from VEP.

Criteria logic summary:
  PM4 and BP3 are mutually exclusive (same consequence, split by repeat region).
  PP2 and BP1 are mutually exclusive (split by gene missense constraint vs LOF-only).
  PM5 requires RAG lookup — only fires for missense variants.

State fields read:
  gene, variant_id, consequence, protein_position, amino_acid_change,
  repeat_region (bool from VEP/post_process),
  gene_clingen_validity, gene_clingen_mechanism,
  gnomad_mis_z, gnomad_oe_mis,
  clinvar_classification, clinvar_review_stars

State fields written:
  agent_evidence["agent8"]
"""

import logging
from src.utils.logging_config import get_user_friendly_logger
from typing import Optional
from src.pipeline.state import VariantState
from src.rag.retriever import query_clinvar_same_codon, query_clinvar_for_gene
from src.utils.llm_client import call_llm_json
from src.utils.disease_matcher import diseases_match, get_disease_match_confidence

logger = get_user_friendly_logger('agent8_gene_context')

# ---------------------------------------------------------------------------
# Consequence groups
# ---------------------------------------------------------------------------
INFRAME_CONSEQUENCES = {
    "inframe_insertion", "inframe_deletion",
    "stop_lost", "start_lost",
}
MISSENSE_CONSEQUENCES = {"missense_variant"}

# gnomAD missense constraint thresholds
# Z >= 3.09 → gene intolerant to missense (supports PP2)
# oe_mis <= 0.6 → also supports PP2 (overlapping metric)
GNOMAD_MIS_Z_THRESHOLD  = 3.09
GNOMAD_OE_MIS_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# PM4 — Protein length change in non-repeat region
# ---------------------------------------------------------------------------

def _evaluate_pm4(
    consequence: str,
    repeat_region: bool,
) -> tuple[Optional[str], list[str]]:
    notes = []
    if consequence not in INFRAME_CONSEQUENCES:
        notes.append(f"PM4 not applicable: consequence={consequence}.")
        return None, notes
    if repeat_region:
        notes.append(
            f"PM4 not assigned: in-frame indel/stop-loss but variant is in a "
            f"repeat region (RepeatMasker). BP3 evaluated instead."
        )
        return None, notes
    notes.append(
        f"PM4 (Moderate): {consequence} causes protein length change "
        f"outside a repeat region. In-frame changes in non-repeat regions "
        f"are evidence of pathogenicity."
    )
    return "Moderate", notes


# ---------------------------------------------------------------------------
# BP3 — In-frame indel in repeat region
# ---------------------------------------------------------------------------

def _evaluate_bp3(
    consequence: str,
    repeat_region: bool,
) -> tuple[Optional[str], list[str]]:
    notes = []
    if consequence not in {"inframe_insertion", "inframe_deletion"}:
        notes.append(f"BP3 not applicable: consequence={consequence}.")
        return None, notes
    if not repeat_region:
        notes.append(
            "BP3 not assigned: in-frame indel but NOT in a repeat region "
            "(PM4 evaluated instead)."
        )
        return None, notes
    notes.append(
        f"BP3 (Supporting): In-frame {consequence} located in a repeat region "
        f"(RepeatMasker). Repeat-region indels are typically tolerated."
    )
    return "Supporting", notes


# ---------------------------------------------------------------------------
# PP2 / BP1 — Missense constraint vs LOF-only mechanism
# ---------------------------------------------------------------------------

def _evaluate_pp2_bp1(
    consequence: str,
    gene: str,
    clingen_mechanism: Optional[str],
    gnomad_mis_z: Optional[float],
    gnomad_oe_mis: Optional[float],
) -> tuple[Optional[str], Optional[str], list[str]]:
    """
    Returns (pp2_strength, bp1_strength, notes).
    PP2 and BP1 are mutually exclusive.
    """
    notes = []

    if consequence not in MISSENSE_CONSEQUENCES:
        notes.append(f"PP2/BP1 not applicable: consequence={consequence}.")
        return None, None, notes

    mechanism = (clingen_mechanism or "").upper()

    # BP1: gene where ONLY LOF causes disease → missense is benign supporting
    if "LOSS" in mechanism and "FUNCTION" in mechanism and "ONLY" in mechanism:
        notes.append(
            f"BP1 (Supporting): {gene} disease mechanism is LOF-only "
            f"(ClinGen: {clingen_mechanism}). Missense variants are not expected "
            f"to cause disease in this gene."
        )
        return None, "Supporting", notes

    # PP2: gene with low rate of benign missense (constraint)
    mis_z_hit  = gnomad_mis_z  is not None and gnomad_mis_z  >= GNOMAD_MIS_Z_THRESHOLD
    oe_mis_hit = gnomad_oe_mis is not None and gnomad_oe_mis <= GNOMAD_OE_MIS_THRESHOLD

    if mis_z_hit or oe_mis_hit:
        constraint_str = []
        if mis_z_hit:
            constraint_str.append(f"mis_Z={gnomad_mis_z:.2f} ≥ {GNOMAD_MIS_Z_THRESHOLD}")
        if oe_mis_hit:
            constraint_str.append(f"oe_mis={gnomad_oe_mis:.3f} ≤ {GNOMAD_OE_MIS_THRESHOLD}")
        notes.append(
            f"PP2 (Supporting): {gene} is missense-constrained "
            f"({', '.join(constraint_str)}). Missense variants in constrained "
            f"genes are more likely pathogenic."
        )
        return "Supporting", None, notes

    notes.append(
        f"PP2/BP1 not assigned: {gene} does not meet missense constraint threshold "
        f"(mis_Z={gnomad_mis_z}, oe_mis={gnomad_oe_mis}) and mechanism is not LOF-only."
    )
    return None, None, notes


# ---------------------------------------------------------------------------
# PM5 — Novel missense at same codon as known pathogenic
# ---------------------------------------------------------------------------

def _evaluate_pm5(
    consequence: str,
    gene: str,
    variant_id: str,
    protein_position: Optional[str],
    amino_acid_change: Optional[str],
    matched_orphanet_disease: Optional[str],
) -> tuple[Optional[str], list[str], list[str]]:
    """
    Query clinvar_gene_variants RAG collection for pathogenic missense variants
    at the same protein position but different amino acid change.

    Cross-validates ClinVar disease with patient's Orphanet-matched disease
    to prevent false PM5 when variant is pathogenic for a different disease.

    Returns (strength, notes, citations).
    """
    notes    = []
    citations = []

    if consequence not in MISSENSE_CONSEQUENCES:
        notes.append(f"PM5 not applicable: consequence={consequence}.")
        return None, notes, citations

    if not protein_position:
        notes.append(
            "PM5 not evaluated: protein_position not available in state. "
            "Check VEP post-processing populates protein_position."
        )
        return None, notes, citations

    # Build RAG query: same gene + same codon position + pathogenic
    query = (
        f"pathogenic missense variant in {gene} at protein position {protein_position} "
        f"different amino acid change"
    )
    try:
        rag_results = query_clinvar_same_codon(
            gene=gene,
            protein_pos=int(protein_position),
            n_results=5,
        )
    except Exception as exc:
        logger.warning(f" PM5 RAG query failed: {exc}")
        notes.append(f"PM5 RAG query failed: {exc}. PM5 not evaluated.")
        return None, notes, citations

    if not rag_results:
        notes.append(
            f"PM5 not assigned: no pathogenic missense variants found in "
            f"ClinVar at {gene} position {protein_position}."
        )
        return None, notes, citations

    # Filter: same position, different AA change, pathogenic/likely pathogenic
    hits = []
    for hit in rag_results:
        meta = hit.get("metadata", {})
        hit_pos = meta.get("protein_pos")
        hit_sig = str(meta.get("clnsig", "")).upper()
        # retriever already filters to same codon ±2; just confirm P/LP signal
        if any(p in hit_sig for p in ("PATHOGENIC", "LIKELY_PATHOGENIC")):
            hits.append(meta)

    if not hits:
        notes.append(
            f"PM5 not assigned: RAG returned results for {gene} at position "
            f"{protein_position} but none are P/LP in ClinVar."
        )
        return None, notes, citations

    # PM5 fires — check disease context if available
    best_hit = hits[0] if hits else {}
    clinvar_disease = best_hit.get("clndn", "")
    base_strength = "Moderate"  # PM5 default strength

    hit_descriptions = "; ".join(
        f"pos={h.get('protein_pos','?')} {h.get('alt','?')} ({h.get('clnsig','?')})"
        for h in hits[:3]
    )

    # Disease matching cross-validation
    if clinvar_disease and matched_orphanet_disease:
        match, similarity, match_note = get_disease_match_confidence(
            clinvar_disease, matched_orphanet_disease
        )

        if match:
            # SAME codon + SAME disease → Standard PM5
            notes.append(
                f"PM5 ({base_strength}): Novel missense at {gene} p.{protein_position}. "
                f"Known pathogenic variant(s) at same codon: {hit_descriptions}. "
                f"ClinVar disease '{clinvar_disease}' matches patient disease "
                f"'{matched_orphanet_disease}' (similarity={similarity:.2f}). "
                f"Disease context validated."
            )
        else:
            # SAME codon + DIFFERENT disease → PM5 with caution, downgraded
            base_strength = "Supporting"
            notes.append(
                f"PM5 ({base_strength}, with caution): Novel missense at {gene} p.{protein_position}. "
                f"Known pathogenic variant(s) at same codon: {hit_descriptions}. "
                f"⚠️ Disease mismatch: ClinVar reports pathogenic for '{clinvar_disease}' "
                f"but patient has '{matched_orphanet_disease}' (similarity={similarity:.2f}). "
                f"PM5 downgraded - verify phenotype relevance."
            )
    else:
        # Disease context unavailable - standard PM5
        notes.append(
            f"PM5 ({base_strength}): Novel missense at {gene} p.{protein_position}. "
            f"Known pathogenic variant(s) at same codon: {hit_descriptions}. "
            f"Different nucleotide change at same position supports pathogenicity."
        )

    citations = [
        f"ClinVar: {h.get('gene','?')}:{h.get('chrom','?')}:{h.get('pos','?')}:{h.get('alt','?')}"
        for h in hits[:3]
    ]
    return base_strength, notes, citations

# ---------------------------------------------------------------------------
# LLM refinement
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ACMG/AMP variant classification expert evaluating gene context evidence.
You assess PM4, PM5, PP2, BP1, BP3.

Key rules:
- PM4 (Moderate): In-frame indel or stop-loss OUTSIDE repeat region causes protein length change.
  Do NOT assign if variant is in a tandem repeat or low-complexity region.
- BP3 (Supporting): In-frame indel INSIDE repeat region. Mutually exclusive with PM4.
- PP2 (Supporting): Missense in gene with low benign missense rate (constrained gene).
  Use gnomAD mis_Z ≥ 3.09 or oe_mis ≤ 0.6 as threshold.
  Do NOT assign if gene mechanism is LOF-only.
- BP1 (Supporting): Missense in gene where ONLY truncating variants cause disease (LOF-only mechanism).
  Mutually exclusive with PP2.
- PM5 (Moderate): Novel missense at same codon as established pathogenic missense (different AA).
  Requires ClinVar evidence of pathogenic variant at same position with different change.
  Do NOT assign PM5 if the same amino acid change is already known (that would be PS1 via agent4).

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
    citations: list[str],
) -> dict:
    gene             = state.get("gene", "UNKNOWN")
    variant_id       = state.get("variant_id", "?")
    consequence      = state.get("consequence", "")
    protein_pos      = state.get("protein_position") or "unknown"
    aa_change        = state.get("amino_acid_change") or "unknown"
    repeat_region    = state.get("repeat_region", False)
    clingen_mech     = state.get("gene_clingen_mechanism") or "unknown"
    clingen_validity = state.get("gene_clingen_validity") or "unknown"
    gnomad_mis_z     = state.get("gnomad_mis_z")
    gnomad_oe_mis    = state.get("gnomad_oe_mis")

    user_prompt = f"""Evaluate gene context evidence for this variant:

Gene: {gene} | Variant: {variant_id}
Consequence: {consequence}
Protein position: {protein_pos} | AA change: {aa_change}
In repeat region: {repeat_region}
ClinGen disease validity: {clingen_validity}
ClinGen disease mechanism: {clingen_mech}
gnomAD missense Z-score: {gnomad_mis_z}
gnomAD oe_mis: {gnomad_oe_mis}

Rule-based pre-evaluation:
  Pathogenic criteria: {criteria_p}
  Benign criteria: {criteria_b}
  Notes: {'; '.join(all_notes)}
  RAG citations: {citations}

Evaluate PM4, PM5, PP2, BP1, BP3 given the above.
Confirm or correct the rule-based assignments.
Note any criteria that should be upgraded, downgraded, or removed."""

    return call_llm_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def agent8_gene_context(state: VariantState) -> dict:
    """
    Agent 8: Evaluate gene context criteria (PM4, PM5, PP2, BP1, BP3).
    Uses clinvar_gene_variants RAG collection for PM5.
    Rule-based logic for PM4, BP3, PP2, BP1 from VEP annotations.

    Returns:
        dict with key "agent_evidence" -> {"agent8": AgentEvidence dict}
    """
    gene        = state.get("gene", "UNKNOWN")
    variant_id  = state.get("variant_id", "?")
    consequence = state.get("consequence", "") or ""
    logger.info(f" Evaluating {variant_id} ({gene}), csq={consequence}")

    repeat_region    = state.get("repeat_region", False) or False
    clingen_mech     = state.get("gene_clingen_mechanism")
    gnomad_mis_z     = state.get("gnomad_mis_z")
    gnomad_oe_mis    = state.get("gnomad_oe_mis")
    protein_position = state.get("protein_position")
    amino_acid_change = state.get("amino_acid_change")

    criteria_p: dict = {}
    criteria_b: dict = {}
    all_notes:  list[str] = []
    all_citations: list[str] = ["ACMG/AMP 2015"]

    # --- PM4 ---
    pm4_strength, pm4_notes = _evaluate_pm4(consequence, repeat_region)
    if pm4_strength:
        criteria_p["PM4"] = pm4_strength
    all_notes.extend(pm4_notes)

    # --- BP3 ---
    bp3_strength, bp3_notes = _evaluate_bp3(consequence, repeat_region)
    if bp3_strength:
        criteria_b["BP3"] = bp3_strength
    all_notes.extend(bp3_notes)

    # --- PP2 / BP1 ---
    pp2_strength, bp1_strength, constraint_notes = _evaluate_pp2_bp1(
        consequence, gene, clingen_mech, gnomad_mis_z, gnomad_oe_mis
    )
    if pp2_strength:
        criteria_p["PP2"] = pp2_strength
    if bp1_strength:
        criteria_b["BP1"] = bp1_strength
    all_notes.extend(constraint_notes)

    # --- PM5 (RAG) ---
    matched_orphanet_disease = state.get("matched_orphanet_disease")
    pm5_strength, pm5_notes, pm5_citations = _evaluate_pm5(
        consequence, gene, variant_id, protein_position, amino_acid_change, matched_orphanet_disease
    )
    if pm5_strength:
        criteria_p["PM5"] = pm5_strength
    all_notes.extend(pm5_notes)
    all_citations.extend(pm5_citations)

    # --- LLM refinement ---
    # Run LLM when any criteria assigned, or for missense in constrained/known genes
    needs_llm = bool(criteria_p or criteria_b) or (
        consequence in MISSENSE_CONSEQUENCES and
        state.get("gene_clingen_validity") not in (None, "", "No Reported Evidence")
    )

    if needs_llm:
        logger.debug(f" Calling LLM for {variant_id}")
        llm_result = _llm_refine(state, criteria_p, criteria_b, all_notes, all_citations)
        if llm_result and not llm_result.get("error"):
            criteria_p     = llm_result.get("criteria_pathogenic", criteria_p)
            criteria_b     = llm_result.get("criteria_benign", criteria_b)
            confidence     = llm_result.get("confidence", "MEDIUM")
            evidence_notes = llm_result.get("evidence_notes", " ".join(all_notes))
            all_citations += llm_result.get("citations", [])
        else:
            logger.warning(f" LLM failed — rule-based only")
            confidence     = "MEDIUM" if (criteria_p or criteria_b) else "LOW"
            evidence_notes = " ".join(all_notes)
    else:
        confidence     = "LOW"
        evidence_notes = (
            f"No gene context criteria applicable for {gene} "
            f"(consequence={consequence}, repeat={repeat_region})."
        )

    all_citations = list(dict.fromkeys(all_citations))

    logger.info(
        f"[agent8] {variant_id}: P={criteria_p} B={criteria_b} conf={confidence}"
    )

    return {
        "agent_evidence": {
            "agent8": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign":     criteria_b,
                "evidence_notes":      evidence_notes,
                "citations":           all_citations,
                "confidence":          confidence,
            }
        }
    }

