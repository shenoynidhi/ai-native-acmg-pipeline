"""
src/agents/agent4_database.py

Agent 4 — Database / Prior Classification Evidence
Evaluates: PS1, PS4, PP5, BP6

ACMG/AMP 2015 criteria assessed:
  PS1  — Same amino acid change as a previously established pathogenic variant
          (different nucleotide change). Pathogenic Strong.
  PS4  — Variant prevalence in affected individuals significantly increased vs controls.
          Requires case-control frequency analysis (OR > 5.0, p < 0.05).
          Uses user-provided case database + gnomAD controls.
          If no case DB provided: NOT_EVALUATED (gnomAD alone insufficient).
  PP5  — Reputable source (ClinVar ≥2 star) reports variant as pathogenic.
          Pathogenic Supporting.
  BP6  — Reputable source (ClinVar ≥2 star) reports variant as benign/likely benign.
          Benign Supporting.

RAG used:
  query_clinvar_by_variant  — exact + gene-level ClinVar lookup
  query_clinvar_same_codon  — same amino acid change lookup (PS1)

Note on PS1 vs PM5:
  PS1: same amino acid change, different nucleotide (e.g. p.Arg175His from C>T vs C>A)
  PM5: different amino acid change at same codon — handled by Agent 8
  This agent handles PS1 only.

State fields read:
  variant_id, gene, consequence, protein_position, hgvsp, hgvsc,
  clinvar_classification, clinvar_review_stars, clinvar_disease, clinvar_accession,
  max_gnomad_af, gnomad_af_popmax

State fields written (via agent_evidence):
  agent_evidence["agent4"]
"""

import logging
from src.utils.logging_config import get_user_friendly_logger
import re
from pathlib import Path
from typing import Optional

from src.pipeline.state import VariantState
from src.rag.retriever import query_clinvar_by_variant, query_clinvar_same_codon
from src.utils.llm_client import call_llm_json
from src.utils.disease_matcher import diseases_match, get_disease_match_confidence
from src.pipeline.pubmed import pubmed_search, pubmed_format_for_llm
from src.utils.case_control import load_case_database, evaluate_ps4
from src.utils.criteria_normalizer import normalize_criteria_dict

logger = get_user_friendly_logger('agent4_database')

# Global cache for case database (load once per session)
_CASE_DB_CACHE = None
_CASE_DB_PATH = None

# ---------------------------------------------------------------------------
# Significance classification helpers
# ---------------------------------------------------------------------------

PATHOGENIC_TERMS = {"pathogenic", "likely_pathogenic"}
BENIGN_TERMS     = {"benign", "likely_benign"}

def _is_pathogenic(clnsig: str) -> bool:
    s = clnsig.lower().replace(" ", "_")
    return any(t in s for t in PATHOGENIC_TERMS) and "conflicting" not in s

def _is_benign(clnsig: str) -> bool:
    s = clnsig.lower().replace(" ", "_")
    return any(t in s for t in BENIGN_TERMS) and "conflicting" not in s

def _is_conflicting(clnsig: str) -> bool:
    return "conflicting" in clnsig.lower()


def _extract_protein_change(hgvsp: Optional[str]) -> Optional[str]:
    """
    Extract the amino acid change from HGVSp.
    e.g. "NP_009225.1:p.Arg175His" → "Arg175His"
    """
    if not hgvsp:
        return None
    m = re.search(r"p\.([A-Za-z]{3}\d+[A-Za-z]{3})", hgvsp)
    return m.group(1) if m else None


def _same_aa_change(hgvsp_query: Optional[str], hit_text: str) -> bool:
    """
    Check whether a ClinVar hit encodes the same amino acid change as our variant.
    Looks for the protein change string in the hit document text.
    """
    aa_change = _extract_protein_change(hgvsp_query)
    if not aa_change:
        return False
    return aa_change.lower() in hit_text.lower()


# ---------------------------------------------------------------------------
# Rule-based evaluation
# ---------------------------------------------------------------------------

def _evaluate_from_direct_clinvar(
    clnsig: Optional[str],
    stars: int,
    accession: Optional[str],
) -> tuple[dict, dict, list[str]]:
    """
    Evaluate PP5/BP6 from the variant's own ClinVar annotation (already in state
    from VEP ClinVar custom annotation — no RAG needed for exact match).
    """
    criteria_p: dict = {}
    criteria_b: dict = {}
    notes = []

    if not clnsig:
        return criteria_p, criteria_b, notes

    if stars >= 2:
        if _is_pathogenic(clnsig):
            criteria_p["PP5"] = "Supporting"
            notes.append(
                f"PP5: ClinVar reports variant as {clnsig} with {stars} stars "
                f"(accession: {accession or 'N/A'})."
            )
        elif _is_benign(clnsig):
            criteria_b["BP6"] = "Supporting"
            notes.append(
                f"BP6: ClinVar reports variant as {clnsig} with {stars} stars "
                f"(accession: {accession or 'N/A'})."
            )
        elif _is_conflicting(clnsig):
            notes.append(
                f"ClinVar shows conflicting interpretations ({clnsig}, {stars} stars). "
                f"PP5/BP6 not assigned."
            )
    elif stars == 1:
        # 1-star only — downgrade to note, don't assign PP5/BP6
        notes.append(
            f"ClinVar {clnsig} ({stars} star) — insufficient stars for PP5/BP6 "
            f"(requires ≥2 stars)."
        )

    return criteria_p, criteria_b, notes


def _evaluate_ps1_from_rag(
    gene: str,
    hgvsp: Optional[str],
    protein_pos: Optional[int],
    consequence: str,
    rag_hits: list[dict],
    matched_orphanet_disease: Optional[str],
) -> tuple[Optional[str], list[str]]:
    """
    PS1: Same amino acid change as a known P/LP variant (different nucleotide).
    Only applies to missense variants.

    Cross-validates ClinVar disease with patient's Orphanet-matched disease
    to prevent false PS1 when variant is pathogenic for a different disease.

    Returns (strength, notes).
    """
    MISSENSE_CONSEQUENCES = {
        "missense_variant",
        "protein_altering_variant",
    }
    if consequence not in MISSENSE_CONSEQUENCES:
        return None, []

    notes = []
    same_aa_hits = [
        h for h in rag_hits
        if _is_pathogenic(h["metadata"].get("clnsig", "")) and
           h["metadata"].get("stars", 0) >= 2 and
           _same_aa_change(hgvsp, h["text"])
    ]

    if not same_aa_hits:
        return None, []

    best = max(same_aa_hits, key=lambda h: h["metadata"].get("stars", 0))
    stars = best["metadata"].get("stars", 0)
    clinvar_disease = best["metadata"].get("clndn", "")

    # Base strength from ClinVar stars
    base_strength = "Strong" if stars >= 3 else "Moderate"

    # Disease matching cross-validation
    if clinvar_disease and matched_orphanet_disease:
        match, similarity, match_note = get_disease_match_confidence(
            clinvar_disease, matched_orphanet_disease
        )

        if match:
            # SAME variant + SAME disease → High confidence PS1
            strength = base_strength
            notes.append(
                f"PS1 ({strength}): Same amino acid change as ClinVar P/LP variant "
                f"({best['metadata'].get('chrom')}:{best['metadata'].get('pos')}, "
                f"{stars} stars). HGVSp={hgvsp}. "
                f"ClinVar disease '{clinvar_disease}' matches patient disease "
                f"'{matched_orphanet_disease}' (similarity={similarity:.2f}). "
                f"High confidence - disease context validated."
            )
        else:
            # SAME variant + DIFFERENT disease → Downgrade strength, flag caution
            strength = "Moderate" if base_strength == "Strong" else "Supporting"
            notes.append(
                f"PS1 ({strength}, with caution): Same amino acid change as ClinVar P/LP variant "
                f"({best['metadata'].get('chrom')}:{best['metadata'].get('pos')}, "
                f"{stars} stars). HGVSp={hgvsp}. "
                f"⚠️ Disease mismatch: ClinVar reports pathogenic for '{clinvar_disease}' "
                f"but patient has '{matched_orphanet_disease}' (similarity={similarity:.2f}). "
                f"PS1 downgraded - verify phenotype relevance."
            )
    elif clinvar_disease:
        # ClinVar has disease but patient disease unknown
        strength = base_strength
        notes.append(
            f"PS1 ({strength}): Same amino acid change as ClinVar P/LP variant "
            f"({best['metadata'].get('chrom')}:{best['metadata'].get('pos')}, "
            f"{stars} stars) for '{clinvar_disease}'. HGVSp={hgvsp}. "
            f"Patient disease not provided - unable to cross-validate disease context."
        )
    else:
        # Neither disease available
        strength = base_strength
        notes.append(
            f"PS1 ({strength}): Same amino acid change as ClinVar P/LP variant "
            f"({best['metadata'].get('chrom')}:{best['metadata'].get('pos')}, "
            f"{stars} stars). HGVSp={hgvsp}. "
            f"Disease context unavailable - classification based on variant identity only."
        )

    return strength, notes


def _evaluate_ps4_from_case_db(
    state: VariantState,
    variant_id: str,
    gene: str,
    case_db_path: Optional[Path],
) -> tuple[Optional[str], list[str]]:
    """
    PS4: Variant prevalence in affected individuals significantly increased vs controls.

    ACMG/AMP 2015 PS4 definition:
        "Prevalence of the variant in affected individuals is significantly increased
         compared with the prevalence in controls (OR > 5.0, p < 0.05)."

    Requires:
        - Case cohort with affected individuals + variant status
        - Control frequencies from gnomAD
        - Fisher's exact test for statistical significance

    If case_db_path is None:
        Returns NOT_EVALUATED with explanation (gnomAD alone is insufficient).

    Returns:
        (strength, notes) where strength = "Strong" | "Supporting" | None
    """
    global _CASE_DB_CACHE, _CASE_DB_PATH

    # No case database provided
    if not case_db_path or not Path(case_db_path).exists():
        return None, [
            "PS4: Not evaluated (requires case-control frequency data). "
            "gnomAD provides control frequencies only; case cohort required. "
            "To enable: provide case_database_csv in config with affected individuals."
        ]

    # Load case database (cached)
    try:
        if _CASE_DB_PATH != case_db_path:
            logger.info(f"Loading case database: {case_db_path}")
            _CASE_DB_CACHE = load_case_database(Path(case_db_path))
            _CASE_DB_PATH = case_db_path

        case_db = _CASE_DB_CACHE
    except Exception as e:
        logger.error(f"Failed to load case database: {e}")
        return None, [f"PS4: Case database load failed ({e})"]

    # Extract disease phenotype from state (if available)
    condition = state.get("phenotype") or state.get("disease")

    # Evaluate PS4 using case-control analysis
    try:
        result = evaluate_ps4(
            case_db=case_db,
            state=state,
            variant_id=variant_id,
            gene=gene,
            condition=condition,
        )
    except Exception as e:
        logger.error(f"PS4 evaluation failed: {e}")
        return None, [f"PS4: Evaluation failed ({e})"]

    # Map result to ACMG strength
    if result["status"] == "met":
        strength = result["strength"]  # "Strong" or "Supporting"
        notes = [
            f"PS4 ({strength}): {result['reason']} "
            f"Cases: {result['cases_with_variant']}/{result['cases_total']} "
            f"({result['case_frequency']:.4f}). "
            f"Controls (gnomAD {result['population']}): "
            f"{result['controls_with_variant']}/{result['controls_total']} "
            f"({result['control_frequency']:.6f}). "
            f"OR={result['OR']}, p={result['p_value']:.4e}."
        ]
        return strength, notes

    elif result["status"] == "not_met":
        return None, [
            f"PS4: Not met. {result['reason']} "
            f"(OR={result['OR']}, p={result['p_value']:.4e})"
        ]

    else:  # insufficient
        return None, [f"PS4: Insufficient evidence. {result['reason']}"]


# ---------------------------------------------------------------------------
# LLM refinement
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ACMG/AMP variant classification expert evaluating prior
classification and database evidence. You assess PS1, PS4, PP5, and BP6 criteria.

Key rules:
- PP5/BP6 require ClinVar ≥2 stars from a reputable expert panel or multiple submitters.
  1-star single submitter is insufficient.
- PS1 requires the SAME amino acid change (not just same gene or same codon) classified
  as pathogenic. Different amino acid at same codon = PM5 (Agent 8), not PS1.
- PS4_Supporting is appropriate when ClinVar has multiple independent P/LP reports but
  case-control data is unavailable.
- Conflicting interpretations in ClinVar → do NOT assign PP5 or BP6.
- If the variant itself is in ClinVar with ≥2 stars, PP5/BP6 takes precedence over
  PS1 (same classification is stronger evidence).

Respond ONLY with a JSON object. No preamble, no markdown fences. Schema:
{
  "criteria_pathogenic": {},
  "criteria_benign": {},
  "evidence_notes": "string — 3-5 sentences",
  "citations": ["sources"],
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "ps1_applies": true | false,
  "pp5_applies": true | false,
  "bp6_applies": true | false,
  "conflicting_evidence": true | false
}"""


def _llm_refine(
    state: VariantState,
    rule_criteria_p: dict,
    rule_criteria_b: dict,
    rag_hits: list[dict],
    pubmed_hits: list[dict],
    notes: list[str],
) -> dict:
    gene       = state.get("gene", "UNKNOWN")
    hgvsc      = state.get("hgvsc") or "N/A"
    hgvsp      = state.get("hgvsp") or "N/A"
    clnsig     = state.get("clinvar_classification") or "Not in ClinVar"
    stars      = state.get("clinvar_review_stars", 0)
    accession  = state.get("clinvar_accession") or "N/A"
    disease    = state.get("clinvar_disease") or "N/A"
    consequence = state.get("consequence", "")
    protein_pos = state.get("protein_position")

    # Summarise top RAG hits for LLM context
    hit_summaries = []
    for h in rag_hits[:8]:
        m = h["metadata"]
        hit_summaries.append(
            f"  {m.get('chrom')}:{m.get('pos')} {m.get('ref')}>{m.get('alt')} "
            f"| {m.get('clnsig')} | {m.get('stars')} stars | gene={m.get('gene')}"
        )
    pubmed_section = pubmed_format_for_llm(pubmed_hits)
    user_prompt = f"""Evaluate database/prior classification evidence for this variant:

Gene: {gene}
Consequence: {consequence}
HGVSc: {hgvsc}
HGVSp: {hgvsp}
Protein position: {protein_pos}

Direct ClinVar annotation (from VEP):
  Significance: {clnsig}
  Stars: {stars}
  Accession: {accession}
  Disease: {disease}

RAG-retrieved ClinVar hits (same gene/region):
{chr(10).join(hit_summaries) or '  No hits retrieved'}

PubMed literature (variant classification):
{pubmed_section}

Rule-based pre-evaluation:
  Pathogenic criteria: {rule_criteria_p}
  Benign criteria: {rule_criteria_b}
  Notes: {'; '.join(notes)}

Please evaluate PS1, PS4, PP5, and BP6. Be conservative:
- Only assign PS1 if you can confirm the SAME amino acid change (check HGVSp in hits)
- Only assign PP5/BP6 if ClinVar stars ≥2 for THIS specific variant
- Flag conflicting_evidence=true if you see both P and B evidence
"""
    return call_llm_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def agent4_database(state: VariantState) -> dict:
    """
    Agent 4: Evaluate prior classification / database criteria (PS1, PS4, PP5, BP6).

    PS4 evaluation uses optional case database from state["case_database_csv"].
    If not provided, PS4 returns NOT_EVALUATED (gnomAD alone is insufficient).

    Returns:
        dict with key "agent_evidence" -> {"agent4": AgentEvidence dict}
    """
    gene        = state.get("gene", "UNKNOWN")
    variant_id  = state.get("variant_id", "?")
    consequence = state.get("consequence", "") or ""
    case_db_path_str = state.get("case_database_csv")
    case_db_path = Path(case_db_path_str) if case_db_path_str else None

    logger.info(f"[agent4_database] Evaluating {variant_id} ({gene})")

    criteria_p: dict = {}
    criteria_b: dict = {}
    all_notes:  list[str] = []
    citations = [
        "ACMG/AMP 2015",
        "ClinVar (NCBI)",
        "Landrum et al. 2016 (ClinVar)",
    ]

    # --- Step 1: Direct ClinVar annotation (exact variant, from VEP) ---
    clnsig    = state.get("clinvar_classification")
    stars     = state.get("clinvar_review_stars", 0) or 0
    accession = state.get("clinvar_accession")

    direct_p, direct_b, direct_notes = _evaluate_from_direct_clinvar(
        clnsig, stars, accession
    )
    criteria_p.update(direct_p)
    criteria_b.update(direct_b)
    all_notes.extend(direct_notes)

    # --- Step 2: RAG lookup — nearby variants in same gene/region ---
    chrom, pos, ref, alt = _parse_variant_id(variant_id)
    protein_pos = state.get("protein_position")
    hgvsp       = state.get("hgvsp")

    rag_hits = []
    try:
        rag_hits = query_clinvar_by_variant(
            chrom=chrom, pos=pos, ref=ref, alt=alt,
            gene=gene, n_results=15,
        )
        logger.debug(f"[agent4] RAG returned {len(rag_hits)} ClinVar hits for {variant_id}")
    except Exception as e:
        logger.warning(f"[agent4] RAG query failed: {e}")
        all_notes.append(f"ClinVar RAG query failed: {e}")

# --- Step 2b: PubMed search — variant classification literature ---
    pubmed_hits = []
    try:
        pubmed_hits = pubmed_search(
            gene=gene,
            hgvsp=state.get("hgvsp"),
            hgvsc=state.get("hgvsc"),
            query_type="variant",
            max_results=10,
        )
        logger.debug(f"[agent4] PubMed returned {len(pubmed_hits)} papers for {variant_id}")
    except Exception as e:
        logger.warning(f"[agent4] PubMed search failed: {e}")
        all_notes.append(f"PubMed search failed: {e}")

    # --- Step 3: PS1 from RAG (only if PP5 not already assigned for same variant) ---
    matched_orphanet_disease = state.get("matched_orphanet_disease")
    if "PP5" not in criteria_p and rag_hits:
        ps1_strength, ps1_notes = _evaluate_ps1_from_rag(
            gene, hgvsp, protein_pos, consequence, rag_hits, matched_orphanet_disease
        )
        if ps1_strength:
            criteria_p["PS1"] = ps1_strength
            all_notes.extend(ps1_notes)

    # --- Step 4: PS4 from case-control analysis ---
    if "PP5" not in criteria_p:
        ps4_strength, ps4_notes = _evaluate_ps4_from_case_db(
            state, variant_id, gene, case_db_path
        )
        if ps4_strength and "PS1" not in criteria_p:
            # Don't stack PS4 if PS1 already assigned (same evidence type)
            criteria_p["PS4"] = ps4_strength
        all_notes.extend(ps4_notes)

    # --- Step 5: LLM refinement ---
    # Call LLM when: no ClinVar direct hit, conflicting signals, or RAG has high-star hits
    high_star_hits = [h for h in rag_hits if h["metadata"].get("stars", 0) >= 3]
    needs_llm = (
        not clnsig or                           # not in ClinVar directly
        _is_conflicting(clnsig or "") or        # conflicting interpretations
        (len(high_star_hits) > 0 and            # RAG found strong evidence
         "PP5" not in criteria_p and
         "BP6" not in criteria_b) or
        (criteria_p and criteria_b)             # both P and B assigned — needs arbitration
    )

    if needs_llm:
        logger.debug(f"[agent4] Calling LLM for {variant_id}")
        llm_result = _llm_refine(state, criteria_p, criteria_b, rag_hits, pubmed_hits, all_notes)

        if llm_result and not llm_result.get("error"):
            # Normalize LLM output using shared normalizer
            llm_criteria_p = normalize_criteria_dict(
                llm_result.get("criteria_pathogenic", {})
            )
            llm_criteria_b = normalize_criteria_dict(
                llm_result.get("criteria_benign", {})
            )

            criteria_p = llm_criteria_p if llm_criteria_p else criteria_p
            criteria_b = llm_criteria_b if llm_criteria_b else criteria_b
            confidence = llm_result.get("confidence", "MEDIUM")
            evidence_notes = llm_result.get("evidence_notes", " ".join(all_notes))
            citations += llm_result.get("citations", [])
            if llm_result.get("conflicting_evidence"):
                all_notes.append("CONFLICT: Both pathogenic and benign ClinVar evidence present.")
        else:
            logger.warning(f"[agent4] LLM failed — using rule-based results")
            confidence = "MEDIUM"
            evidence_notes = " ".join(all_notes) or (
                f"No ClinVar evidence found for {gene} {variant_id}. "
                f"PS1, PS4, PP5, BP6 not assigned."
            )
    else:
        confidence = "HIGH" if (criteria_p or criteria_b) else "MEDIUM"
        evidence_notes = " ".join(all_notes) or (
            f"No qualifying ClinVar evidence for {gene} {variant_id}. "
            f"PP5/BP6 require ≥2 stars; PS1 requires same amino acid change classified P/LP."
        )

    citations = list(dict.fromkeys(citations))
    logger.info(
        f"[agent4] {variant_id}: P={criteria_p} B={criteria_b} conf={confidence}"
    )

    return {
        "agent_evidence": {
            "agent4": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign":     criteria_b,
                "evidence_notes":      evidence_notes,
                "citations":           citations,
                "confidence":          confidence,
            }
        }
    }


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _parse_variant_id(variant_id: str) -> tuple[str, int, str, str]:
    """
    Parse "chr13:32339657:A:AT" → ("chr13", 32339657, "A", "AT").
    Falls back to safe defaults on parse error.
    """
    try:
        parts = variant_id.split(":")
        chrom = parts[0]
        pos   = int(parts[1])
        ref   = parts[2]
        alt   = parts[3]
        return chrom, pos, ref, alt
    except Exception:
        return "chrUnknown", 0, "N", "N"

