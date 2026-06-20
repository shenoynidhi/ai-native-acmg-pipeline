"""
src/pipeline/nodes/debate_final_arbiter.py

Final Arbiter — Debate Layer Node 3 of 3.

Role: Receives the preliminary classification, both advocate arguments, and
all agent evidence. Applies ACMG Table 5 combination rules one final time
with any newly proposed criteria from the advocates. Produces the confirmed
final classification with a human-readable evidence summary.

This is the most sophisticated LLM call in the pipeline. The arbiter must:
  1. Accept or reject each advocate's proposed upgrades/additions with explicit
     ACMG/ClinGen justification.
  2. Re-apply Table 5 combination rules with the accepted final criteria set.
  3. Handle conflicts — if both pathogenic and benign strong evidence exist,
     classify as VUS and specify what would change it.
  4. For VUS: specify what evidence would be needed to reclassify.
  5. Flag any unevaluated criteria (PP4/BP5 when no clinical input).

RAG queries:
  - acmg_guidelines: ALL fired criteria from both advocates + combination rules
  - Query is broadest of the three debate nodes

State fields READ:
  variant_id, gene, consequence, hgvs_p, hgvs_c,
  all_criteria_pathogenic, all_criteria_benign,
  pathogenic_counts, benign_counts,
  preliminary_classification, conflict_flag,
  classification_rules_met, aggregator_notes,
  agent_evidence, unevaluated_criteria,
  pathogenic_advocate_result, benign_advocate_result

State fields WRITTEN:
  final_classification:           str   (P/LP/VUS/LB/B)
  criteria_applied:               list  (["PVS1:Very Strong", "PM2:Supporting", ...])
  evidence_summary:               str   (3-5 sentence human-readable)
  confidence:                     str   (HIGH/MEDIUM/LOW)
  recommended_followup:           str
  variant_reclassification_conditions: str  (VUS only — what would change it)
  debate_notes:                   str   (arbiter's reasoning on advocate disagreements)
  unevaluated_criteria_report:    list  (criteria skipped due to missing input)
"""

import json
import logging
from src.utils.logging_config import get_user_friendly_logger
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions

from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json
from src.config import CHROMADB_DIR

logger = get_user_friendly_logger('final_arbiter')

EMBEDDING_MODEL = "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# RAG helpers
# ---------------------------------------------------------------------------

def _get_chroma_collection(name: str):
    from src.rag.chromadb_client import get_chromadb_client
    client = get_chromadb_client(CHROMADB_DIR)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )
    return client.get_collection(name, embedding_function=ef)


def _query_acmg_guidelines_arbiter(
    all_fired_criteria: list[str],
    proposed_criteria: list[str],
    gene: str,
    consequence: str,
) -> list[str]:
    """
    Broadest RAG query — all fired + proposed criteria, combination rules.
    The arbiter needs the full picture to make the final call.
    """
    try:
        col = _get_chroma_collection("acmg_guidelines")

        all_criteria = list(set(all_fired_criteria + proposed_criteria))
        criteria_str = " ".join(all_criteria) if all_criteria else "ACMG classification"

        query = (
            f"{criteria_str} combination rules final classification "
            f"upgrade downgrade conflict {consequence} {gene}"
        )

        # No side filter — arbiter needs both P and B guideline context
        results = col.query(query_texts=[query], n_results=7)
        docs = results["documents"][0] if results["documents"] else []

        # Always include combination rules and upgrade/downgrade reference
        for anchor_id in ("COMBINATION_RULES", "UPGRADE_DOWNGRADE_RULES"):
            anchor = col.get(ids=[anchor_id])
            if anchor["documents"]:
                anchor_text = anchor["documents"][0]
                if anchor_text not in docs:
                    docs.append(anchor_text)

        return docs

    except Exception as e:
        logger.warning(f"[final_arbiter] RAG query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt(guideline_chunks: list[str]) -> str:
    guidelines_text = "\n\n---\n\n".join(guideline_chunks) if guideline_chunks else "No guideline context retrieved."

    return f"""You are a senior clinical laboratory director making the final ACMG/AMP 2015
variant classification decision after reviewing arguments from both a pathogenic
advocate and a benign advocate.

Your responsibilities:
1. Review both advocate arguments objectively. Accept or reject each proposed
   criterion upgrade with explicit justification citing ACMG or ClinGen rules.
2. Re-apply ACMG Table 5 combination rules using your final accepted criteria set.
3. Conflict resolution rules:
   a. If the preliminary classification is Pathogenic or Likely_Pathogenic AND
      no Stand-alone (BA1) or Strong (BS1-BS4) benign criteria are present,
      you MUST maintain P or LP. Absence of benign evidence is NOT a reason
      to call VUS — it simply means benign evidence is lacking.
   b. Only downgrade to VUS if there is ACTIVE contradicting evidence:
      at least one Strong benign criterion (BS) that genuinely conflicts
      with pathogenic criteria of similar weight.
   c. A weak benign argument from the advocate (no strong BS criteria) does
      NOT justify VUS when pathogenic criteria clearly meet LP or P thresholds.
4. For VUS classifications: specify EXACTLY what evidence would be needed to
   reclassify (e.g., "functional study demonstrating loss of enzymatic activity
   would add PS3 Moderate and upgrade to LP via LP2").
5. Flag all unevaluated criteria (PP4/BP5 if no clinical history was provided).
6. For trio mode variants with PM6 (assumed de novo): We already HAVE parental
   genotypes showing the variant is absent in both parents. The recommended followup
   should request "confirmation of parental identity via maternity/paternity testing"
   to upgrade PM6 to PS2, NOT "obtain parental genotypes" (we have them).
7. Your final classification must be reproducible — another reviewer reading your
   output should reach the same conclusion from the same evidence.

ACMG GUIDELINE REFERENCE (retrieved for all criteria active in this debate):
{guidelines_text}

Output format — respond with valid JSON only, no markdown:
{{
  "final_classification": "Pathogenic|Likely_Pathogenic|VUS|Likely_Benign|Benign",
  "final_criteria_applied": ["criterion:strength", ...],
  "criteria_rejected_from_advocates": [{{"criterion": "...", "reason": "..."}}],
  "evidence_summary": "3-5 sentence human-readable summary of the final classification rationale",
  "confidence": "HIGH|MEDIUM|LOW",
  "recommended_followup": "specific recommended tests or data that would strengthen or change the classification",
  "reclassification_conditions": "for VUS: what specific evidence would reclassify this variant; for P/LP/B/LB: conditions under which reclassification should be triggered",
  "debate_notes": "1-3 sentences on how the two advocate arguments were weighed",
  "unevaluated_criteria_report": ["list of criteria not evaluated due to missing input, e.g. PP4_not_evaluated_no_phenotype_match, BP5_not_evaluated_no_alternate_molecular_diagnosis"]
}}"""


def _build_user_prompt(state: VariantState) -> str:
    variant_id  = state.get("variant_id", "unknown")
    gene        = state.get("gene", "unknown")
    consequence = state.get("consequence", "unknown")
    hgvs_p = state.get("hgvsp", "")
    hgvs_c = state.get("hgvsc", "")
    prelim      = state.get("preliminary_classification", "VUS")
    rules_met   = state.get("classification_rules_met", [])
    agg_notes   = state.get("aggregator_notes", "")
    criteria_p  = state.get("all_criteria_pathogenic", {})
    criteria_b  = state.get("all_criteria_benign", {})
    p_counts    = state.get("pathogenic_counts", {})
    b_counts    = state.get("benign_counts", {})
    conflict    = state.get("conflict_flag", False)
    unevaluated = state.get("unevaluated_criteria", [])
    agent_ev    = state.get("agent_evidence", {})

    p_advocate  = state.get("pathogenic_advocate_result", {})
    b_advocate  = state.get("benign_advocate_result", {})

    # Format advocate summaries
    p_adv_text = "No pathogenic advocate result."
    if p_advocate:
        p_adv_text = (
            f"Classification argued: {p_advocate.get('advocate_classification','?')} "
            f"(confidence: {p_advocate.get('confidence','?')})\n"
            f"Additional criteria proposed: {p_advocate.get('additional_criteria_proposed',[])}\n"
            f"Proposed upgrades: {p_advocate.get('upgraded_criteria',{})}\n"
            f"Rationale: {p_advocate.get('rationale','')}"
        )

    b_adv_text = "No benign advocate result."
    if b_advocate:
        b_adv_text = (
            f"Classification argued: {b_advocate.get('advocate_classification','?')} "
            f"(confidence: {b_advocate.get('confidence','?')})\n"
            f"Additional criteria proposed: {b_advocate.get('additional_criteria_proposed',[])}\n"
            f"Proposed upgrades: {b_advocate.get('upgraded_criteria',{})}\n"
            f"Rationale: {b_advocate.get('rationale','')}"
        )

    # Per-agent evidence
    agent_summaries = []
    for agent_key in sorted(agent_ev.keys()):
        ev = agent_ev[agent_key]
        if not isinstance(ev, dict):
            continue
        notes = ev.get("evidence_notes", "")
        cp    = ev.get("criteria_pathogenic", {})
        cb    = ev.get("criteria_benign", {})
        if cp or cb or notes:
            agent_summaries.append(
                f"{agent_key}: P={cp} B={cb} | {notes[:200]}"
            )

    return f"""VARIANT: {variant_id}
Gene: {gene} | Consequence: {consequence}
HGVSp: {hgvs_p} | HGVSc: {hgvs_c}

════════════════════════════════════════
PRELIMINARY CLASSIFICATION: {prelim}
ACMG RULES MET BY AGGREGATOR: {rules_met}
CONFLICT FLAG: {conflict}
════════════════════════════════════════

AGGREGATED PATHOGENIC CRITERIA (from agents 1-9):
{json.dumps(criteria_p, indent=2)}

AGGREGATED BENIGN CRITERIA (from agents 1-9):
{json.dumps(criteria_b, indent=2)}

PATHOGENIC COUNTS: PVS={p_counts.get('Very Strong',0)} PS={p_counts.get('Strong',0)} PM={p_counts.get('Moderate',0)} PP={p_counts.get('Supporting',0)}
BENIGN COUNTS: BA={b_counts.get('Stand-alone',0)} BS={b_counts.get('Strong',0)} BP={b_counts.get('Supporting',0)}

UNEVALUATED CRITERIA (missing input): {unevaluated}

AGGREGATOR NOTES: {agg_notes}

════════════════════════════════════════
PATHOGENIC ADVOCATE ARGUMENT:
{p_adv_text}

════════════════════════════════════════
BENIGN ADVOCATE ARGUMENT:
{b_adv_text}

════════════════════════════════════════
PER-AGENT EVIDENCE SUMMARIES:
{chr(10).join(agent_summaries)}

════════════════════════════════════════
As the final arbiter:
1. Accept or reject each advocate's proposed criteria with explicit justification.
2. Re-apply Table 5 rules with your final accepted criteria set.
3. Produce the confirmed classification.
4. If VUS: specify exactly what would reclassify this variant.
5. Flag unevaluated criteria in your report.
Respond in the required JSON format."""


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

def debate_final_arbiter_node(state: VariantState) -> dict:
    """
    Final arbiter debate node.
    Synthesises both advocate arguments and produces the confirmed
    ACMG classification with full evidence summary.
    """
    variant_id = state.get("variant_id", "?")
    logger.info(f"[final_arbiter] Processing {variant_id}")

    # Collect ALL active criteria for broadest RAG query
    fired_p  = list(state.get("all_criteria_pathogenic", {}).keys())
    fired_b  = list(state.get("all_criteria_benign", {}).keys())

    p_adv    = state.get("pathogenic_advocate_result", {})
    b_adv    = state.get("benign_advocate_result", {})

    proposed_p = p_adv.get("additional_criteria_proposed", []) if p_adv else []
    proposed_b = b_adv.get("additional_criteria_proposed", []) if b_adv else []

    # Strip strength suffixes for RAG query (e.g. "PM3:Strong_upgrade" → "PM3")
    def _extract_criterion_code(s: str) -> str:
        return s.split(":")[0].split("_")[0].strip()

    all_proposed = [_extract_criterion_code(c) for c in proposed_p + proposed_b]
    all_fired    = fired_p + fired_b

    gene        = state.get("gene", "")
    consequence = state.get("consequence", "")

    guideline_chunks = _query_acmg_guidelines_arbiter(
        all_fired, all_proposed, gene, consequence
    )

    logger.info(
        f"[final_arbiter] RAG: {len(guideline_chunks)} guideline chunks"
    )

    system_prompt = _build_system_prompt(guideline_chunks)
    user_prompt   = _build_user_prompt(state)

    raw_result = call_llm_json(system_prompt, user_prompt)

    result = _validate_arbiter_output(raw_result, state, variant_id)

    # Override LLM classification with deterministic Table 5 enforcement
    llm_classification = result["final_classification"]
    enforced_classification = _enforce_table5_classification(
        result["final_criteria_applied"],
        state.get("preliminary_classification", "VUS"),
    )

    if enforced_classification != llm_classification:
        logger.warning(
            f"[final_arbiter] {variant_id}: LLM said '{llm_classification}' but "
            f"Table 5 enforcement overrides to '{enforced_classification}'. "
            f"Criteria: {result['final_criteria_applied']}"
        )
        result["final_classification"] = enforced_classification

    # Compute Tavtigian points for comparison
    tavtigian_points, tavtigian_class = _tavtigian_points(result["final_criteria_applied"])

    # Log if Table 5 and Tavtigian disagree (informational, don't override)
    if tavtigian_class != result["final_classification"]:
        logger.info(
            f"[final_arbiter] {variant_id}: Table5={result['final_classification']}, "
            f"Tavtigian={tavtigian_class} ({tavtigian_points}pts) — using Table 5"
        )

    logger.info(
        f"[final_arbiter] {variant_id}: "
        f"final_classification={result['final_classification']} "
        f"confidence={result['confidence']}"
    )

    # Return flat fields — these are the terminal classification outputs
    return {
        "final_classification":               result["final_classification"],
        "final_criteria_applied":                   result["final_criteria_applied"],
        "evidence_summary":                   result["evidence_summary"],
        "confidence":                         result["confidence"],
        "recommended_followup":               result["recommended_followup"],
        "reclassification_conditions": result["reclassification_conditions"],
        "debate_notes":                       result["debate_notes"],
        "unevaluated_criteria_report":        result["unevaluated_criteria_report"],
        "tavtigian_points":                   tavtigian_points,
        "tavtigian_classification":           tavtigian_class,
    }


# ---------------------------------------------------------------------------
# Classification enforcement — Table 5 and Tavtigian deterministic rules
# ---------------------------------------------------------------------------

def _enforce_table5_classification(criteria_applied: list, preliminary: str) -> str:
    """
    Deterministic ACMG Table 5 (Richards 2015) + Tavtigian 2020 point system.
    Returns standard 5-tier ACMG classification.
    """
    pvs = ps = pm = pp = ba = bs = bp = 0

    for crit in criteria_applied:
        parts  = crit.split(":")
        code   = parts[0].strip().upper()
        strength = parts[1].strip().lower() if len(parts) > 1 else ""

        is_benign = code.startswith(("BA", "BS", "BP"))

        if is_benign:
            if code.startswith("BA") or "stand" in strength:
                ba += 1
            elif code.startswith("BS") or "strong" in strength:
                bs += 1
            elif code.startswith("BP") or "support" in strength:
                bp += 1
        else:
            if code.startswith("PVS") or "very strong" in strength:
                pvs += 1
            elif code.startswith("PS") or (strength == "strong"):
                ps += 1
            elif code.startswith("PM") or "moderate" in strength:
                pm += 1
            elif code.startswith("PP") or "support" in strength:
                pp += 1

    # ── Pathogenic ────────────────────────────────────────────────────────
    pathogenic = (
        (pvs >= 1 and ps >= 1)                          # P1
        or (pvs >= 1 and pm >= 2)                       # P2
        or (pvs >= 1 and pm >= 1 and pp >= 1)           # P3
        or (pvs >= 1 and pp >= 2)                       # P4
        or (ps >= 2)                                    # P5
        or (ps >= 1 and pm >= 3)                        # P6
        or (ps >= 1 and pm >= 2 and pp >= 2)            # P7
        or (ps >= 1 and pm >= 1 and pp >= 4)            # P8
    )
    if pathogenic:
        return "Pathogenic"

    # ── Likely Pathogenic ─────────────────────────────────────────────────
    likely_path = (
        (pvs >= 1 and pm >= 1)                          # LP1
        or (ps >= 1 and (pm == 1 or pm == 2))           # LP2
        or (ps >= 1 and pp >= 2)                        # LP3
        or (pm >= 3)                                    # LP4
        or (pm >= 2 and pp >= 2)                        # LP5
        or (pm >= 1 and pp >= 4)                        # LP6
    )
    if likely_path:
        return "Likely_Pathogenic"

    # ── Benign ────────────────────────────────────────────────────────────
    if ba >= 1 or bs >= 2:
        return "Benign"

    # ── Likely Benign ─────────────────────────────────────────────────────
    if (bs >= 1 and bp >= 1) or bp >= 2:
        return "Likely_Benign"

    return "VUS"


def _tavtigian_points(criteria_applied: list) -> tuple:
    """
    Tavtigian 2020 point-based classification.
    Returns (total_points, classification_string).
    """
    STRENGTH_POINTS = {
        "very strong": 8, "pvs": 8,
        "strong":      4, "ps":  4,
        "moderate":    2, "pm":  2,
        "supporting":  1, "pp":  1,
    }
    BENIGN_POINTS = {
        "supporting":  -1, "bp": -1,
        "strong":      -4, "bs": -4,
        "stand-alone": -8, "ba": -8,
    }

    total = 0
    for crit in criteria_applied:
        parts    = crit.split(":")
        code     = parts[0].strip().upper()
        strength = parts[1].strip().lower() if len(parts) > 1 else ""

        is_benign = code.startswith(("BA", "BS", "BP"))

        if is_benign:
            # Try strength string first, then code prefix
            pts = BENIGN_POINTS.get(strength)
            if pts is None:
                for prefix, val in [("BA", -8), ("BS", -4), ("BP", -1)]:
                    if code.startswith(prefix):
                        pts = val
                        break
            total += (pts or 0)
        else:
            pts = STRENGTH_POINTS.get(strength)
            if pts is None:
                for prefix, val in [("PVS", 8), ("PS", 4), ("PM", 2), ("PP", 1)]:
                    if code.startswith(prefix):
                        pts = val
                        break
            total += (pts or 0)

    if total >= 10:
        classification = "Pathogenic"
    elif total >= 6:
        classification = "Likely_Pathogenic"
    elif total >= 0:
        classification = "VUS"
    elif total >= -6:
        classification = "Likely_Benign"
    else:
        classification = "Benign"

    return total, classification


def _validate_arbiter_output(raw: dict, state: VariantState, variant_id: str) -> dict:
    """Validate arbiter output, fall back to preliminary classification on failure."""
    valid_classifications = {
        "Pathogenic", "Likely_Pathogenic", "VUS", "Likely_Benign", "Benign"
    }
    valid_confidence = {"HIGH", "MEDIUM", "LOW"}

    final_class = raw.get("final_classification", "")
    if final_class not in valid_classifications:
        # Hard fallback: use preliminary classification rather than silently outputting garbage
        fallback = state.get("preliminary_classification", "VUS")
        logger.error(
            f"[final_arbiter] Invalid final_classification '{final_class}' for "
            f"{variant_id} — falling back to preliminary: {fallback}"
        )
        final_class = fallback

    confidence = raw.get("confidence", "LOW")
    if confidence not in valid_confidence:
        confidence = "LOW"

    unevaluated = state.get("unevaluated_criteria", [])
    unevaluated_report = raw.get("unevaluated_criteria_report", [])
    # Merge: always include pipeline-level unevaluated criteria in report
    # Map each unevaluated criterion to its correct reason flag
    _UNEVALUATED_REASONS = {
        "PP4": "PP4_not_evaluated_no_phenotype_match",
        "BP5": "BP5_not_evaluated_no_alternate_molecular_diagnosis",
    }
    for crit in unevaluated:
        flag = _UNEVALUATED_REASONS.get(crit, f"{crit}_not_evaluated_no_input_data")
        if flag not in unevaluated_report:
            unevaluated_report.append(flag)
    return {
        "final_classification":               final_class,
        "final_criteria_applied":                   raw.get("final_criteria_applied", []),
        "criteria_rejected_from_advocates":   raw.get("criteria_rejected_from_advocates", []),
        "evidence_summary":                   raw.get("evidence_summary", "No evidence summary provided."),
        "confidence":                         confidence,
        "recommended_followup":               raw.get("recommended_followup", ""),
        "reclassification_conditions": raw.get("reclassification_conditions", ""),
        "debate_notes":                       raw.get("debate_notes", ""),
        "unevaluated_criteria_report":        unevaluated_report,
    }

