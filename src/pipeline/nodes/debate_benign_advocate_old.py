"""
src/pipeline/nodes/debate_benign_advocate.py

Benign Advocate — Debate Layer Node 2 of 3.

Role: Acts as a clinical laboratory scientist arguing for the MOST BENIGN
interpretation supported by the evidence. Checks whether benign criteria
were missed, underweighted, or if population/functional data supports
a more benign call.

RAG queries:
  - acmg_guidelines collection: retrieve definitions for benign criteria
    fired, plus combination rules (benign side)
  - clinvar_variants collection: check for any B/LB ClinVar entries for
    this variant

State fields READ:
  (same as pathogenic_advocate — full state)

State fields WRITTEN:
  benign_advocate_result: {
      "advocate_classification":       str,
      "additional_criteria_proposed":  list,
      "upgraded_criteria":             dict,  # benign upgrades
      "rationale":                     str,
      "rag_evidence_used":             list,
      "confidence":                    str,
  }
"""

import json
import logging
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions

from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json
from src.config import CHROMADB_DIR

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# RAG helpers
# ---------------------------------------------------------------------------

def _get_chroma_collection(name: str):
    client = chromadb.PersistentClient(path=str(CHROMADB_DIR))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )
    return client.get_collection(name, embedding_function=ef)


def _query_acmg_guidelines_benign(fired_criteria_b: list[str], gene: str, consequence: str) -> list[str]:
    """
    Retrieve ACMG guideline chunks relevant to fired benign criteria.
    Query emphasises benign side — missed criteria, population data, BP checks.
    """
    try:
        col = _get_chroma_collection("acmg_guidelines")

        criteria_str = " ".join(fired_criteria_b) if fired_criteria_b else "benign evidence"
        query = (
            f"{criteria_str} benign population frequency alternate diagnosis "
            f"functional benign {consequence} {gene}"
        )

        results = col.query(
            query_texts=[query],
            n_results=5,
            where={"side": {"$in": ["benign", "both"]}},
        )

        docs = results["documents"][0] if results["documents"] else []

        # Always include combination rules
        for anchor_id in ("COMBINATION_RULES", "UPGRADE_DOWNGRADE_RULES"):
            anchor = col.get(ids=[anchor_id])
            if anchor["documents"]:
                anchor_text = anchor["documents"][0]
                if anchor_text not in docs:
                    docs.append(anchor_text)

        return docs

    except Exception as e:
        logger.warning(f"[benign_advocate] RAG query failed: {e}")
        return []


def _query_clinvar_benign(hgvs_p: Optional[str], hgvs_c: Optional[str], gene: str) -> list[str]:
    """Query clinvar_variants for any B/LB entries for this variant."""
    try:
        col = _get_chroma_collection("clinvar_variants")
        query_parts = [gene]
        if hgvs_p:
            query_parts.append(hgvs_p)
        if hgvs_c:
            query_parts.append(hgvs_c)
        query = " ".join(query_parts) + " benign likely benign polymorphism"

        results = col.query(query_texts=[query], n_results=3)
        return results["documents"][0] if results["documents"] else []
    except Exception as e:
        logger.warning(f"[benign_advocate] ClinVar RAG query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_system_prompt(guideline_chunks: list[str]) -> str:
    guidelines_text = "\n\n---\n\n".join(guideline_chunks) if guideline_chunks else "No guideline context retrieved."

    return f"""You are a senior clinical laboratory scientist and molecular geneticist.
Your role in this analysis is the BENIGN ADVOCATE.

Your job is NOT to be balanced. Your job is to make the strongest scientifically
defensible argument FOR benignity, based ONLY on evidence that is actually present
in the variant data and agent outputs provided to you.

Specifically, you must check:
1. Was BA1 correctly evaluated? (AF > 5% in any gnomAD population)
2. Were BS1/BS2 thresholds applied correctly for this inheritance mode?
3. Is there any ClinVar B/LB evidence that was missed or underweighted?
4. Does the variant co-occur with a confirmed pathogenic variant in another gene
   (BP5 — alternate molecular diagnosis)?
5. Are computational tools truly agreeing on benign prediction (BP4)?
6. Is this a synonymous variant with no splice impact (BP7)?
7. Is the gene one where only truncating variants cause disease (BP1)?
8. Was any benign functional evidence (BS3) missed?

Rules you must follow:
1. Do NOT invent evidence not present in the agent outputs.
2. DO propose specific benign criteria that may have been missed with justification.
3. If the evidence genuinely does not support a benign call, say so honestly.
   A weak advocate argument is still a valid output.

ACMG GUIDELINE REFERENCE (retrieved for this variant's specific criteria):
{guidelines_text}

Output format — respond with valid JSON only, no markdown:
{{
  "advocate_classification": "Pathogenic|Likely_Pathogenic|VUS|Likely_Benign|Benign",
  "additional_criteria_proposed": ["criterion:strength", ...],
  "upgraded_criteria": {{"criterion": "new_strength", ...}},
  "rationale": "3-5 sentence argument for the most benign supported interpretation",
  "rag_evidence_used": ["brief description of each RAG chunk that influenced reasoning"],
  "confidence": "HIGH|MEDIUM|LOW"
}}"""


def _build_user_prompt(state: VariantState, clinvar_chunks: list[str]) -> str:
    variant_id   = state.get("variant_id", "unknown")
    gene         = state.get("gene", "unknown")
    consequence  = state.get("consequence", "unknown")
    hgvs_p       = state.get("hgvs_p", "")
    hgvs_c       = state.get("hgvs_c", "")
    prelim       = state.get("preliminary_classification", "VUS")
    rules_met    = state.get("classification_rules_met", [])
    agg_notes    = state.get("aggregator_notes", "")
    criteria_p   = state.get("all_criteria_pathogenic", {})
    criteria_b   = state.get("all_criteria_benign", {})
    p_counts     = state.get("pathogenic_counts", {})
    b_counts     = state.get("benign_counts", {})
    conflict     = state.get("conflict_flag", False)
    unevaluated  = state.get("unevaluated_criteria", [])
    agent_ev     = state.get("agent_evidence", {})

    # Also pass pathogenic advocate's result for context (it ran first)
    p_advocate   = state.get("pathogenic_advocate_result", {})
    p_adv_summary = ""
    if p_advocate:
        p_adv_summary = (
            f"\nPATHOGENIC ADVOCATE ARGUED: {p_advocate.get('advocate_classification','?')} "
            f"(confidence: {p_advocate.get('confidence','?')})\n"
            f"Their rationale: {p_advocate.get('rationale','')[:300]}"
        )

    clinvar_text = ""
    if clinvar_chunks:
        clinvar_text = "\n\nCLINVAR RAG RESULTS (benign entries):\n" + "\n---\n".join(clinvar_chunks)

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

PRELIMINARY CLASSIFICATION: {prelim}
ACMG RULES MET: {rules_met}
CONFLICT FLAG: {conflict}

AGGREGATED PATHOGENIC CRITERIA: {json.dumps(criteria_p)}
AGGREGATED BENIGN CRITERIA: {json.dumps(criteria_b)}

PATHOGENIC COUNTS: PVS={p_counts.get('Very Strong',0)} PS={p_counts.get('Strong',0)} PM={p_counts.get('Moderate',0)} PP={p_counts.get('Supporting',0)}
BENIGN COUNTS: BA={b_counts.get('Stand-alone',0)} BS={b_counts.get('Strong',0)} BP={b_counts.get('Supporting',0)}

UNEVALUATED CRITERIA (missing clinical input): {unevaluated}

AGGREGATOR NOTES: {agg_notes}
{p_adv_summary}

PER-AGENT EVIDENCE SUMMARIES:
{chr(10).join(agent_summaries)}
{clinvar_text}

As the benign advocate, review all evidence above.
Identify the strongest defensible benign classification.
Check for missed benign criteria, population data, and any ClinVar benign support.
Respond in the required JSON format."""


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

def debate_benign_advocate_node(state: VariantState) -> dict:
    """
    Benign advocate debate node.
    Queries RAG for guideline context (benign side), then calls LLM
    to argue for the most benign defensible interpretation.
    """
    variant_id = state.get("variant_id", "?")
    logger.info(f"[benign_advocate] Processing {variant_id}")

    fired_b     = list(state.get("all_criteria_benign", {}).keys())
    gene        = state.get("gene", "")
    consequence = state.get("consequence", "")
    hgvs_p      = state.get("hgvs_p", "")
    hgvs_c      = state.get("hgvs_c", "")

    # RAG queries — benign-focused
    guideline_chunks = _query_acmg_guidelines_benign(fired_b, gene, consequence)
    clinvar_chunks   = _query_clinvar_benign(hgvs_p, hgvs_c, gene)

    logger.info(
        f"[benign_advocate] RAG: {len(guideline_chunks)} guideline chunks, "
        f"{len(clinvar_chunks)} ClinVar chunks"
    )

    system_prompt = _build_system_prompt(guideline_chunks)
    user_prompt   = _build_user_prompt(state, clinvar_chunks)

    raw_result = call_llm_json(system_prompt, user_prompt)

    result = _validate_advocate_output(raw_result, variant_id)

    logger.info(
        f"[benign_advocate] {variant_id}: "
        f"advocate_classification={result['advocate_classification']} "
        f"confidence={result['confidence']}"
    )

    return {"benign_advocate_result": result}


def _validate_advocate_output(raw: dict, variant_id: str) -> dict:
    valid_classifications = {
        "Pathogenic", "Likely_Pathogenic", "VUS", "Likely_Benign", "Benign"
    }
    valid_confidence = {"HIGH", "MEDIUM", "LOW"}

    classification = raw.get("advocate_classification", "VUS")
    if classification not in valid_classifications:
        logger.warning(
            f"[benign_advocate] Invalid classification '{classification}' "
            f"for {variant_id} — defaulting to VUS"
        )
        classification = "VUS"

    confidence = raw.get("confidence", "LOW")
    if confidence not in valid_confidence:
        confidence = "LOW"

    return {
        "advocate_classification":      classification,
        "additional_criteria_proposed": raw.get("additional_criteria_proposed", []),
        "upgraded_criteria":            raw.get("upgraded_criteria", {}),
        "rationale":                    raw.get("rationale", "No rationale provided."),
        "rag_evidence_used":            raw.get("rag_evidence_used", []),
        "confidence":                   confidence,
    }
