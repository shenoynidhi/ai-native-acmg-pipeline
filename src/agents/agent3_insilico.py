"""
src/agents/agent3_insilico.py

Agent 3 — In-Silico Predictor Criteria
Evaluates: PP3, BP4, BP7

ACMG/AMP 2015 criteria assessed:
  PP3  — Multiple in-silico predictors support damaging effect → Pathogenic Supporting
         (upgraded to PP3_Moderate per ClinGen SVI 2023 refinements if ≥5 tools agree)
  BP4  — Multiple in-silico predictors support benign/tolerated → Benign Supporting
  BP7  — Synonymous variant with no predicted splice effect → Benign Supporting

Predictors used (from VEP + dbNSFP, already in state):
  REVEL       — primary for missense (threshold: ≥0.75 damaging, <0.15 benign)
  CADD_phred  — secondary (≥25 damaging, <15 benign)
  SIFT        — ≤0.05 damaging, >0.1 benign
  PolyPhen2   — ≥0.908 damaging ("probably"), <0.446 benign
  MutationTaster — supporting
  MetaSVM     — meta-predictor
  EVE         — evolutionary model
  SpliceAI    — splice effect (any score ≥0.2 = potential splice impact)
  MaxEntScan  — splice strength change
  GERP++      — conservation (RS ≥2 conserved)
  PhyloP100way — conservation (≥2.5 conserved)

Vote counting: PP3 fires if ≥3 damaging votes; BP4 fires if ≥3 benign votes.
If votes are split, LLM arbitrates.

State fields read:
  revel_score, cadd_phred, sift_score, polyphen2_score, mutationtaster_score,
  metasvm_score, eve_score, max_spliceai, maxentscan_diff, gerp_rs, phylop100way,
  insilico_votes_damaging, insilico_votes_benign,
  consequence, gene, is_loftee_hc

State fields written (via agent_evidence):
  agent_evidence["agent3"]
"""

import logging
from typing import Optional

from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds — sourced from tool-specific literature + ACMG/ClinGen guidance
# ---------------------------------------------------------------------------

REVEL_DAMAGING  = 0.75
REVEL_BENIGN    = 0.15

CADD_DAMAGING   = 25.0
CADD_BENIGN     = 15.0

SIFT_DAMAGING   = 0.05   # lower = more damaging
SIFT_BENIGN     = 0.10

PP2_DAMAGING    = 0.908  # PolyPhen2 "probably damaging"
PP2_BENIGN      = 0.446  # PolyPhen2 "benign"

SPLICEAI_IMPACT = 0.2    # any DS score ≥ this = potential splice effect

GERP_CONSERVED  = 2.0
PHYLOP_CONSERVED = 2.5

# Minimum votes to call PP3 / BP4 by rules
PP3_VOTE_MIN    = 3
BP4_VOTE_MIN    = 3

# PP3 upgrade to Moderate if ≥5 concordant damaging votes (ClinGen SVI 2023)
PP3_MODERATE_VOTES = 5


# ---------------------------------------------------------------------------
# Individual predictor vote functions
# ---------------------------------------------------------------------------

def _vote_revel(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score >= REVEL_DAMAGING:
        return "damaging"
    if score <= REVEL_BENIGN:
        return "benign"
    return None


def _vote_cadd(phred: Optional[float]) -> Optional[str]:
    if phred is None:
        return None
    if phred >= CADD_DAMAGING:
        return "damaging"
    if phred <= CADD_BENIGN:
        return "benign"
    return None


def _vote_sift(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score <= SIFT_DAMAGING:
        return "damaging"
    if score > SIFT_BENIGN:
        return "benign"
    return None


def _vote_polyphen2(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score >= PP2_DAMAGING:
        return "damaging"
    if score < PP2_BENIGN:
        return "benign"
    return None


def _vote_mutationtaster(score: Optional[float]) -> Optional[str]:
    """MutationTaster: >0.5 disease-causing, ≤0.5 polymorphism."""
    if score is None:
        return None
    if score > 0.5:
        return "damaging"
    return "benign"


def _vote_metasvm(score: Optional[float]) -> Optional[str]:
    """MetaSVM: >0 = damaging (tolerated/deleterious split)."""
    if score is None:
        return None
    if score > 0:
        return "damaging"
    return "benign"


def _vote_eve(score: Optional[float]) -> Optional[str]:
    """EVE: ≥0.5 pathogenic, <0.5 benign."""
    if score is None:
        return None
    if score >= 0.5:
        return "damaging"
    return "benign"


def _vote_conservation(gerp: Optional[float], phylop: Optional[float]) -> Optional[str]:
    """Counts as 1 combined conservation vote."""
    conserved = (
        (gerp is not None and gerp >= GERP_CONSERVED) or
        (phylop is not None and phylop >= PHYLOP_CONSERVED)
    )
    not_conserved = (
        (gerp is not None and gerp < 0) or
        (phylop is not None and phylop < 0)
    )
    if conserved:
        return "damaging"  # conservation supports pathogenicity
    if not_conserved:
        return "benign"
    return None


def _has_splice_impact(spliceai: Optional[float], maxentscan_diff: Optional[float]) -> bool:
    """Returns True if any splice predictor suggests splice impact."""
    if spliceai is not None and spliceai >= SPLICEAI_IMPACT:
        return True
    # MaxEntScan: large negative diff = loss of splice site strength
    if maxentscan_diff is not None and maxentscan_diff <= -3.0:
        return True
    return False


# ---------------------------------------------------------------------------
# Vote aggregation
# ---------------------------------------------------------------------------

def _aggregate_votes(state: VariantState) -> tuple[int, int, dict]:
    """
    Collect votes from all predictors.
    Returns: (damaging_votes, benign_votes, vote_detail_dict)
    """
    votes_d = 0
    votes_b = 0
    detail  = {}

    predictors = [
        ("REVEL",           _vote_revel(state.get("revel_score"))),
        ("CADD",            _vote_cadd(state.get("cadd_phred"))),
        ("SIFT",            _vote_sift(state.get("sift_score"))),
        ("PolyPhen2",       _vote_polyphen2(state.get("polyphen2_score"))),
        ("MutationTaster",  _vote_mutationtaster(state.get("mutationtaster_score"))),
        ("MetaSVM",         _vote_metasvm(state.get("metasvm_score"))),
        ("EVE",             _vote_eve(state.get("eve_score"))),
        ("Conservation",    _vote_conservation(
                                state.get("gerp_rs"),
                                state.get("phylop100way")
                            )),
    ]

    for name, vote in predictors:
        if vote == "damaging":
            votes_d += 1
            detail[name] = "damaging"
        elif vote == "benign":
            votes_b += 1
            detail[name] = "benign"
        else:
            detail[name] = "N/A"

    return votes_d, votes_b, detail


# ---------------------------------------------------------------------------
# BP7: Synonymous + no splice impact
# ---------------------------------------------------------------------------

SYNONYMOUS_CONSEQUENCES = {
    "synonymous_variant",
    "stop_retained_variant",
    "incomplete_terminal_codon_variant",
}

def _evaluate_bp7(
    consequence: str,
    spliceai: Optional[float],
    maxentscan_diff: Optional[float],
) -> tuple[bool, str]:
    """
    BP7: Synonymous with no predicted splice disruption.
    Returns (applies: bool, reasoning: str)
    """
    if consequence not in SYNONYMOUS_CONSEQUENCES:
        return False, "Not a synonymous variant"

    if _has_splice_impact(spliceai, maxentscan_diff):
        return False, (
            f"Synonymous variant BUT predicted splice impact "
            f"(SpliceAI={spliceai}, MaxEntScan_diff={maxentscan_diff})"
        )

    return True, (
        f"Synonymous variant ({consequence}) with no predicted splice effect "
        f"(SpliceAI={spliceai or 'N/A'}, MaxEntScan_diff={maxentscan_diff or 'N/A'})"
    )


# ---------------------------------------------------------------------------
# LLM refinement
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ACMG/AMP variant classification expert evaluating in-silico
computational evidence. You assess PP3, BP4, and BP7 criteria following ACMG 2015 guidelines
and ClinGen SVI 2023 refinements for computational evidence calibration.

Respond ONLY with a JSON object. No preamble, no markdown fences. Schema:
{
  "criteria_pathogenic": {},
  "criteria_benign": {},
  "evidence_notes": "string — 3-5 sentences",
  "citations": ["sources"],
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "pp3_fires": true | false,
  "bp4_fires": true | false,
  "bp7_fires": true | false,
  "vote_summary": "brief summary of predictor concordance"
}

PP3 strength: "Supporting" (default) or "Moderate" (≥5 concordant damaging predictors, ClinGen 2023).
BP4 and BP7 are always "Supporting".
If a criterion fires, include it in criteria_pathogenic or criteria_benign with its strength.
REVEL is the primary predictor for missense — weight it most heavily."""


def _llm_refine_insilico(
    state: VariantState,
    votes_d: int,
    votes_b: int,
    vote_detail: dict,
    rule_criteria_p: dict,
    rule_criteria_b: dict,
) -> dict:
    gene        = state.get("gene", "UNKNOWN")
    consequence = state.get("consequence", "")
    revel       = state.get("revel_score")
    cadd        = state.get("cadd_phred")
    sift        = state.get("sift_score")
    pp2         = state.get("polyphen2_score")
    spliceai    = state.get("max_spliceai")
    maxent      = state.get("maxentscan_diff")
    gerp        = state.get("gerp_rs")
    phylop      = state.get("phylop100way")
    eve         = state.get("eve_score")
    metasvm     = state.get("metasvm_score")
    muttaster   = state.get("mutationtaster_score")

    user_prompt = f"""Evaluate in-silico computational evidence for this variant:

Gene: {gene}
Consequence: {consequence}

In-silico scores:
  REVEL:           {revel}  (primary — damaging ≥0.75, benign ≤0.15)
  CADD_phred:      {cadd}   (damaging ≥25, benign <15)
  SIFT:            {sift}   (damaging ≤0.05, tolerated >0.1)
  PolyPhen2:       {pp2}    (probably_damaging ≥0.908, benign <0.446)
  MutationTaster:  {muttaster}
  MetaSVM:         {metasvm}
  EVE:             {eve}
  SpliceAI (max):  {spliceai}  (impact threshold ≥0.2)
  MaxEntScan_diff: {maxent}
  GERP++ RS:       {gerp}
  PhyloP100way:    {phylop}

Vote summary (rule-based):
  Damaging votes: {votes_d}
  Benign votes:   {votes_b}
  Per-predictor:  {vote_detail}

Rule-based criteria assigned:
  Pathogenic: {rule_criteria_p}
  Benign:     {rule_criteria_b}

Please evaluate PP3, BP4, and BP7:
- Weight REVEL most heavily for missense
- Note that split votes (both P and B) → do NOT assign either PP3 or BP4
- BP7 requires synonymous consequence AND no splice prediction support
- PP3 can be upgraded to Moderate if ≥5 damaging predictors agree (ClinGen SVI 2023)
"""
    return call_llm_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def agent3_insilico(state: VariantState) -> dict:
    """
    Agent 3: Evaluate in-silico computational criteria (PP3, BP4, BP7).

    Returns:
        dict with key "agent_evidence" -> {"agent3": AgentEvidence dict}
    """
    gene        = state.get("gene", "UNKNOWN")
    variant_id  = state.get("variant_id", "?")
    consequence = state.get("consequence", "") or ""
    logger.info(f"[agent3_insilico] Evaluating {variant_id} ({gene}) — {consequence}")

    criteria_p: dict = {}
    criteria_b: dict = {}
    citations = [
        "ACMG/AMP 2015",
        "ClinGen SVI computational evidence calibration 2023",
        "Ioannidis et al. 2016 (REVEL)",
        "Kircher et al. 2014 (CADD)",
    ]

    spliceai     = state.get("max_spliceai")
    maxentscan   = state.get("maxentscan_diff")

    # --- BP7 check first (doesn't depend on vote counts) ---
    bp7_applies, bp7_reason = _evaluate_bp7(consequence, spliceai, maxentscan)
    if bp7_applies:
        criteria_b["BP7"] = "Supporting"

    # --- Vote aggregation ---
    # Use pre-computed votes from post_process if available, else recompute
    votes_d = state.get("insilico_votes_damaging", 0) or 0
    votes_b_count = state.get("insilico_votes_benign", 0) or 0

    # Always recompute for detail breakdown
    votes_d, votes_b_count, vote_detail = _aggregate_votes(state)

    # --- PP3 / BP4 rule-based ---
    # Only fire if votes are NOT split (concordant majority)
    is_split = votes_d >= 2 and votes_b_count >= 2

    if not is_split:
        if votes_d >= PP3_VOTE_MIN:
            strength = "Moderate" if votes_d >= PP3_MODERATE_VOTES else "Supporting"
            criteria_p["PP3"] = strength

        elif votes_b_count >= BP4_VOTE_MIN:
            criteria_b["BP4"] = "Supporting"

    # --- SpliceAI override: if splice impact predicted, PP3 fires regardless of missense votes ---
    if _has_splice_impact(spliceai, maxentscan):
        if "PP3" not in criteria_p:
            criteria_p["PP3"] = "Supporting"
            logger.info(f"[agent3] SpliceAI/MaxEntScan triggered PP3 for {variant_id}")
        # Also means BP7 cannot apply
        if "BP7" in criteria_b:
            del criteria_b["BP7"]

    # --- Determine if LLM needed ---
    needs_llm = (
        is_split or                               # ambiguous votes
        (votes_d == 0 and votes_b_count == 0) or  # no scores available
        (votes_d > 0 and votes_b_count > 0 and not is_split)  # borderline
    )

    all_none = all(v == "N/A" for v in vote_detail.values())
    if all_none:
        # No in-silico scores at all — LLM won't help either
        evidence_notes = (
            f"No in-silico predictor scores available for {gene} {consequence}. "
            f"PP3, BP4, BP7 cannot be evaluated."
        )
        confidence = "LOW"
        needs_llm = False

    if needs_llm:
        logger.debug(f"[agent3] Calling LLM for PP3/BP4 on {variant_id}")
        llm_result = _llm_refine_insilico(
            state, votes_d, votes_b_count, vote_detail, criteria_p, criteria_b
        )

        if llm_result and not llm_result.get("error"):
            criteria_p = llm_result.get("criteria_pathogenic", criteria_p)
            criteria_b = llm_result.get("criteria_benign", criteria_b)
            confidence = llm_result.get("confidence", "MEDIUM")
            evidence_notes = llm_result.get("evidence_notes", "")
            citations += llm_result.get("citations", [])
        else:
            logger.warning(f"[agent3] LLM failed for {variant_id} — rule-based only")
            confidence = "LOW"
            evidence_notes = (
                f"In-silico votes split or borderline for {gene}: "
                f"{votes_d} damaging / {votes_b_count} benign. "
                f"LLM unavailable. Criteria assigned by rules: P={criteria_p}, B={criteria_b}."
            )
    else:
        confidence = "HIGH"
        score_summary = ", ".join(
            f"{k}={v}" for k, v in vote_detail.items() if v != "N/A"
        ) or "no scores available"

        if bp7_applies:
            evidence_notes = (
                f"BP7 applies: {bp7_reason}. "
                f"No PP3/BP4 evaluated for synonymous variants."
            )
        elif "PP3" in criteria_p:
            evidence_notes = (
                f"PP3 ({criteria_p['PP3']}): {votes_d} damaging votes from "
                f"({score_summary}). No benign votes."
            )
        elif "BP4" in criteria_b:
            evidence_notes = (
                f"BP4 (Supporting): {votes_b_count} benign votes from "
                f"({score_summary}). No damaging votes."
            )
        else:
            evidence_notes = (
                f"No PP3/BP4/BP7 criteria apply for {gene} {consequence}. "
                f"Votes: {votes_d} damaging, {votes_b_count} benign ({score_summary})."
            )

    citations = list(dict.fromkeys(citations))
    logger.info(
        f"[agent3] {variant_id}: P={criteria_p} B={criteria_b} conf={confidence}"
    )

    return {
        "agent_evidence": {
            "agent3": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign":     criteria_b,
                "evidence_notes":      evidence_notes,
                "citations":           citations,
                "confidence":          confidence,
            }
        }
    }
