"""
src/pipeline/nodes/debate_pathogenic_advocate.py

Pathogenic Advocate — Debate Layer Node 1 of 3.

Role: Acts as a clinical laboratory scientist arguing for the MOST PATHOGENIC
interpretation supported by the evidence. Does NOT fabricate evidence — it
re-examines what agents 1-9 found and checks whether:
  (a) any criterion strength should be upgraded per ClinGen rules
  (b) any applicable criterion was missed or underweighted
  (c) any RAG context (ClinVar, guidelines) supports a stronger call

RAG queries:
  - acmg_guidelines collection: retrieve definitions + upgrade rules for
    each criterion that was fired, focused on pathogenic side
  - clinvar_variants collection: check for any ClinVar P/LP entries for
    this exact variant or same amino-acid change

State fields READ:
  variant_id, gene, consequence, transcript_consequence,
  all_criteria_pathogenic, all_criteria_benign,
  pathogenic_counts, benign_counts,
  preliminary_classification, conflict_flag,
  classification_rules_met, aggregator_notes,
  agent_evidence, unevaluated_criteria,
  hgvs_p, hgvs_c (for ClinVar RAG query)

State fields WRITTEN:
  pathogenic_advocate_result: {
      "advocate_classification":       str,   # P/LP/VUS/LB/B
      "additional_criteria_proposed":  list,  # e.g. ["PM3:Strong_upgrade"]
      "upgraded_criteria":             dict,  # {criterion: new_strength}
      "rationale":                     str,
      "rag_evidence_used":             list,
      "confidence":                    str,   # HIGH/MEDIUM/LOW
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

# Criteria where ClinGen explicitly allows strength upgrades
UPGRADEABLE_CRITERIA = {
    "PVS1": ["Strong", "Moderate"],          # downgrade directions
    "PM3":  ["Strong", "Very Strong"],       # upgrade directions
    "PP1":  ["Moderate", "Strong"],
    "PS2":  ["Very Strong"],
    "PS3":  ["Moderate"],                    # downgrade only
}


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


def _query_acmg_guidelines_pathogenic(fired_criteria: list[str], gene: str, consequence: str) -> list[str]:
    """
    Retrieve ACMG guideline chunks relevant to the fired pathogenic criteria.
    Query emphasises upgrade conditions and pathogenic interpretation.
    Returns list of document strings to inject into the prompt.
    """
    try:
        col = _get_chroma_collection("acmg_guidelines")

        # Build a targeted query from fired criteria
        criteria_str = " ".join(fired_criteria) if fired_criteria else "pathogenic evidence"
        query = (
            f"{criteria_str} upgrade conditions pathogenic strength "
            f"{consequence} {gene}"
        )

        # Always also fetch combination rules and upgrade/downgrade reference
        results = col.query(
            query_texts=[query],
            n_results=5,
            where={"side": {"$in": ["pathogenic", "both"]}},
        )

        docs = results["documents"][0] if results["documents"] else []

        # Ensure combination rules and upgrade rules are always present
        for anchor_id in ("COMBINATION_RULES", "UPGRADE_DOWNGRADE_RULES"):
            anchor = col.get(ids=[anchor_id])
            if anchor["documents"]:
                anchor_text = anchor["documents"][0]
                if anchor_text not in docs:
                    docs.append(anchor_text)

        return docs

    except Exception as e:
        logger.warning(f"[pathogenic_advocate] RAG query failed: {e}")
        return []


def _query_clinvar_for_variant(hgvs_p: Optional[str], hgvs_c: Optional[str], gene: str) -> list[str]:
    """
    Query clinvar_variants collection for any P/LP entries matching this variant.
    Returns list of relevant ClinVar document strings.
    """
    try:
        col = _get_chroma_collection("clinvar_variants")
        query_parts = [gene]
        if hgvs_p:
            query_parts.append(hgvs_p)
        if hgvs_c:
            query_parts.append(hgvs_c)
        query = " ".join(query_parts) + " pathogenic"

        results = col.query(query_texts=[query], n_results=3)
        return results["documents"][0] if results["documents"] else []
    except Exception as e:
        logger.warning(f"[pathogenic_advocate] ClinVar RAG query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_system_prompt(guideline_chunks: list[str]) -> str:
    guidelines_text = "\n\n---\n\n".join(guideline_chunks) if guideline_chunks else "No guideline context retrieved."

    return f"""You are a senior clinical laboratory scientist and molecular geneticist.
Your role in this analysis is the PATHOGENIC ADVOCATE.

Your job is NOT to be balanced. Your job is to make the strongest scientifically
defensible argument FOR pathogenicity, based ONLY on evidence that is actually
present in the variant data and agent outputs provided to you.

Rules you must follow:
1. Do NOT invent evidence that is not in the agent outputs.
2. DO check whether any criterion strength should be upgraded per ClinGen rules.
3. DO check whether any applicable criterion was missed by the agents.
4. DO flag if any criterion was applied too conservatively.
5. Your proposed upgrades must cite a specific ClinGen or ACMG rule that permits them.
6. If the evidence genuinely does not support a pathogenic call, say so — a weak
   advocate argument is still a valid output. Do not overclaim.

ACMG GUIDELINE REFERENCE (retrieved for this variant's specific criteria):
{guidelines_text}

Output format — respond with valid JSON only, no markdown:
{{
  "advocate_classification": "Pathogenic|Likely_Pathogenic|VUS|Likely_Benign|Benign",
  "additional_criteria_proposed": ["criterion:strength", ...],
  "upgraded_criteria": {{"criterion": "new_strength", ...}},
  "rationale": "3-5 sentence argument for the most pathogenic supported interpretation",
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

    clinvar_text = ""
    if clinvar_chunks:
        clinvar_text = "\n\nCLINVAR RAG RESULTS:\n" + "\n---\n".join(clinvar_chunks)

    # Summarise per-agent evidence notes for LLM context
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

PER-AGENT EVIDENCE SUMMARIES:
{chr(10).join(agent_summaries)}
{clinvar_text}

As the pathogenic advocate, review all evidence above.
Identify the strongest defensible pathogenic classification.
Check for missed criteria, possible upgrades, and any ClinVar support.
Respond in the required JSON format."""


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

def debate_pathogenic_advocate_node(state: VariantState) -> dict:
    """
    Pathogenic advocate debate node.
    Queries RAG for guideline context, then calls LLM to argue for pathogenicity.
    """
    variant_id = state.get("variant_id", "?")
    logger.info(f"[pathogenic_advocate] Processing {variant_id}")

    # Extract fired pathogenic criteria for targeted RAG query
    fired_p = list(state.get("all_criteria_pathogenic", {}).keys())
    gene        = state.get("gene", "")
    consequence = state.get("consequence", "")
    hgvs_p      = state.get("hgvs_p", "")
    hgvs_c      = state.get("hgvs_c", "")

    # RAG queries
    guideline_chunks = _query_acmg_guidelines_pathogenic(fired_p, gene, consequence)
    clinvar_chunks   = _query_clinvar_for_variant(hgvs_p, hgvs_c, gene)

    logger.info(
        f"[pathogenic_advocate] RAG: {len(guideline_chunks)} guideline chunks, "
        f"{len(clinvar_chunks)} ClinVar chunks"
    )

    system_prompt = _build_system_prompt(guideline_chunks)
    user_prompt   = _build_user_prompt(state, clinvar_chunks)

    raw_result = call_llm_json(system_prompt, user_prompt)

    # Validate and normalise output
    result = _validate_advocate_output(raw_result, variant_id)

    logger.info(
        f"[pathogenic_advocate] {variant_id}: "
        f"advocate_classification={result['advocate_classification']} "
        f"confidence={result['confidence']}"
    )

    return {"pathogenic_advocate_result": result}


def _validate_advocate_output(raw: dict, variant_id: str) -> dict:
    """Ensure required keys present and values are valid."""
    valid_classifications = {
        "Pathogenic", "Likely_Pathogenic", "VUS", "Likely_Benign", "Benign"
    }
    valid_confidence = {"HIGH", "MEDIUM", "LOW"}

    classification = raw.get("advocate_classification", "VUS")
    if classification not in valid_classifications:
        logger.warning(
            f"[pathogenic_advocate] Invalid classification '{classification}' "
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

