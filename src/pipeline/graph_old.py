"""
src/pipeline/graph.py

LangGraph state machine wiring for the ACMG variant classification pipeline.

Architecture:
  - Each node is a Python function: (VariantState) -> dict
  - Nodes return ONLY the fields they changed — LangGraph merges them into state
  - Stub nodes (prefixed _stub_) pass state through unchanged until implemented
  - Conditional edges handle: VEP skip, BA1 short-circuit, HPO NLP skip

Build order (replace stubs in this sequence):
  Phase 3:  input_validation, prefilter, phasing, annotation_detector
  Phase 4:  vep_runner, post_process
  Phase 5:  RAG build (offline, not a node)
  Phase 6:  agent1 … agent9
  Phase 7:  evidence_aggregator
  Phase 8:  debate nodes (pathogenic_advocate, benign_advocate, final_arbiter)
  Phase 9:  hpo_nlp, hpo_matcher, phenotype_scorer, zygosity_filter
  Phase 10: report_generator
"""

import asyncio
import logging
from typing import Any

from langgraph.graph import StateGraph, END

from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)


# =============================================================================
# STUB NODES
# Replace each stub with the real implementation as you build each phase.
# A stub does nothing except log that it was called and return {}.
# =============================================================================

def _stub(name: str):
    """Factory that returns a named stub node function."""
    def node(state: VariantState) -> dict:
        logger.debug(f"[STUB] {name} called for variant '{state.get('variant_id', '?')}'")
        return {}
    node.__name__ = name
    return node

# --- Pre-processing nodes (Phase 3+4) — real implementations ----------------
from src.pipeline.nodes.input_validation    import validate_input_node
from src.pipeline.nodes.annotation_detector import detect_annotation_node
from src.pipeline.nodes.vep_runner          import vep_runner_node
from src.pipeline.nodes.prefilter           import prefilter_node
from src.pipeline.nodes.phasing             import phasing_node
from src.pipeline.nodes.post_process        import post_process_node

# --- Agent stubs (Phase 6) ---------------------------------------------------
# Replace with: src/agents/agent{N}_{name}.py

from src.agents.agent1_population import agent1_population as agent1_node
# Real: evaluates BA1, BS1, BS2, PM2 (population frequency criteria)

from src.agents.agent2_consequence import agent2_consequence as agent2_node
# Real: evaluates PVS1 (null variant / loss-of-function — 5-caveat decision tree)

from src.agents.agent3_insilico    import agent3_insilico    as agent3_node
# Real: evaluates PP3, BP4, BP7 (in-silico predictor consensus)

from src.agents.agent4_database import agent4_database as agent4_node
# Real: evaluates PS1, PS4, PP5, BP6 — uses RAG (ChromaDB ClinVar collection)

from src.agents.agent5_functional import agent5_functional as agent5_node
# Real: evaluates PS3, BS3, PM1 — uses RAG (UniProt domain collection)

from src.agents.agent6_segregation import agent6_segregation as agent6_node
# Real: evaluates PP1, PM3, BP2, BS4 (segregation / phase evidence)

from src.agents.agent7_denovo import agent7_denovo as agent7_node
# Real: evaluates PS2, PM6 (de novo status — trio mode)

from src.agents.agent8_gene_context import agent8_gene_context as agent8_node
# Real: evaluates PM4, PM5, PP2, BP1, BP3 — uses RAG + RepeatMasker

from src.agents.agent9_phenotype import agent9_phenotype as agent9_node
# Real: evaluates PP4, BP5 (phenotype match to gene/disease)


# --- Evidence aggregator stub (Phase 7) -------------------------------------
from src.pipeline.nodes.evidence_aggregator import evidence_aggregator_node
# Real: applies ACMG Table 5 combination rules → preliminary_classification,
#       sets conflict_flag and ba1_shortcircuit


# --- Debate stubs (Phase 8) -------------------------------------------------
from src.pipeline.nodes.debate_pathogenic_advocate import debate_pathogenic_advocate_node as pathogenic_advocate_node
# Real: LLM argues strongest pathogenic case from agent evidence

from src.pipeline.nodes.debate_benign_advocate import debate_benign_advocate_node as benign_advocate_node
# Real: LLM argues strongest benign case, rebuts pathogenic advocate

from src.pipeline.nodes.debate_final_arbiter import debate_final_arbiter_node as final_arbiter_node
# Real: LLM weighs debate, issues final_classification + evidence_summary


# --- Clinical actionability (Phase 8.5) -------------------------------------
from src.pipeline.nodes.clinical_actionability import clinical_actionability_node
# Real: queries ASCO/NCCN/ESMO/OncoKB/CIViC for therapeutic recommendations


# --- HPO / phenotype stubs (Phase 9) ----------------------------------------
from src.pipeline.nodes.hpo_nlp import hpo_nlp_node as hpo_nlp_node 
# Real: extracts HPO term IDs from clinical_notes free text using LLM

from src.pipeline.nodes.hpo_matcher import hpo_matcher_node  as hpo_matcher_node
# Real: matches patient HPO terms to gene-disease associations in HPO + Orphanet

from src.pipeline.nodes.phenotype_scorer import phenotype_scorer_node as phenotype_scorer_node
# Real: scores each variant's gene against patient HPO terms → phenotype_score

from src.pipeline.nodes.zygosity_filter import zygosity_filter_node as zygosity_filter_node
# Real: checks inheritance pattern vs zygosity → zygosity_filter_status


# --- Report stub (Phase 10) -------------------------------------------------
report_generator_node = _stub("report_generator_node")
# Real: writes ranked Excel + HTML + TSV reports to work_dir/reports/


# =============================================================================
# PARALLEL AGENT EXECUTOR
# Runs all 9 agents simultaneously using asyncio.
# This node is NOT a stub — the parallelism logic is final.
# Individual agent stubs will be replaced without touching this function.
# =============================================================================

def run_agents_in_parallel(state: VariantState) -> dict:
    """
    Dispatch all 9 specialist agents concurrently and merge their evidence.

    Each agent returns a dict like:
        {"agent_evidence": {"agent1": AgentEvidence(...)}}

    This function gathers all 9 results and merges them into a single
    agent_evidence dict keyed agent1…agent9.

    Agents that raise exceptions produce a LOW-confidence empty evidence object
    rather than crashing the whole pipeline.
    """
    agent_fns = [
        ("agent1", agent1_node),
        ("agent2", agent2_node),
        ("agent3", agent3_node),
        ("agent4", agent4_node),
        ("agent5", agent5_node),
        ("agent6", agent6_node),
        ("agent7", agent7_node),
        ("agent8", agent8_node),
        ("agent9", agent9_node),
    ]

    async def _run_all():
        tasks = [
            asyncio.create_task(asyncio.to_thread(fn, state))
            for _, fn in agent_fns
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = asyncio.run(_run_all())

    agent_evidence = {}
    for (agent_key, _), result in zip(agent_fns, results):
        if isinstance(result, Exception):
            logger.error(f"Agent {agent_key} raised: {result}")
            agent_evidence[agent_key] = {
                "criteria_pathogenic": {},
                "criteria_benign":     {},
                "evidence_notes":      f"Agent error: {result}",
                "citations":           [],
                "confidence":          "LOW",
            }
        elif isinstance(result, dict):
            # Agent returns {"agent_evidence": {"agentN": {...}}}
            agent_evidence.update(result.get("agent_evidence", {}))
        else:
            # Stub returns {} — record as empty evidence, not an error
            agent_evidence[agent_key] = {
                "criteria_pathogenic": {},
                "criteria_benign":     {},
                "evidence_notes":      "Stub — not yet implemented",
                "citations":           [],
                "confidence":          "LOW",
            }

    return {"agent_evidence": agent_evidence}


# =============================================================================
# CONDITIONAL EDGE FUNCTIONS
# These read state fields to decide which node to route to next.
# =============================================================================

def _should_run_vep(state: VariantState) -> str:
    """Skip VEP if the input VCF already contains CSQ/ANN annotation fields."""
    if state.get("vep_already_annotated"):
        logger.info("VEP annotation detected in input — skipping VEP runner.")
        return "skip_vep"
    return "run_vep"


def _should_run_debate(state: VariantState) -> str:
    """
    BA1 short-circuit: if a variant has AF > 5% it is Benign by stand-alone rule.
    No debate needed — go straight to final_arbiter for report formatting.
    """
    if state.get("ba1_shortcircuit"):
        logger.info(f"BA1 short-circuit for {state.get('variant_id')} — skipping debate.")
        return "skip_debate"
    prelim = state.get("preliminary_classification", "VUS")
    if prelim in ("Pathogenic", "Benign") and not state.get("conflict_flag"):
        logger.info(f"No Conflict for {state.get('variant_id')} — skipping debate.")
        return "skip_debate"
    return "run_debate"

def _should_run_hpo_nlp(state: VariantState) -> str:
    """
    Run NLP only when raw clinical notes are provided and HPO terms haven't
    been pre-supplied. If neither is available, skip — variants will be
    flagged by hpo_matcher/phenotype_scorer with score=0.0.
    """
    if state.get("patient_hpo_terms"):
        return "skip_nlp"
    if state.get("clinical_notes"):
        return "run_nlp"
    return "skip_nlp"  # neither provided — downstream nodes handle flagging

# =============================================================================
# GRAPH BUILDER
# =============================================================================

def build_variant_graph() -> StateGraph:
    """
    Assemble and return the compiled LangGraph for processing one variant.

    Call once at startup:
        VARIANT_GRAPH = build_variant_graph().compile()

    Then invoke per variant:
        result_state = VARIANT_GRAPH.invoke(initial_state)
    """
    graph = StateGraph(VariantState)

    # -------------------------------------------------------------------------
    # Register nodes
    # -------------------------------------------------------------------------

    # Pre-processing
    graph.add_node("validate_input",      validate_input_node)
    graph.add_node("detect_annotation",   detect_annotation_node)
    graph.add_node("run_vep",             vep_runner_node)
    graph.add_node("prefilter",           prefilter_node)
    graph.add_node("phasing",             phasing_node)
    graph.add_node("post_process",        post_process_node)

    # Parallel agent runner
    graph.add_node("run_agents",          run_agents_in_parallel)

    # Evidence aggregation
    graph.add_node("evidence_aggregator", evidence_aggregator_node)

    # Debate layer
    graph.add_node("pathogenic_advocate", pathogenic_advocate_node)
    graph.add_node("benign_advocate",     benign_advocate_node)
    graph.add_node("final_arbiter",       final_arbiter_node)

    # Clinical actionability
    graph.add_node("clinical_actionability", clinical_actionability_node)

    # HPO / phenotype
    graph.add_node("hpo_nlp",            hpo_nlp_node)
    graph.add_node("hpo_matcher",        hpo_matcher_node)
    graph.add_node("phenotype_scorer",   phenotype_scorer_node)
    graph.add_node("zygosity_filter",    zygosity_filter_node)

    # Output
    graph.add_node("report_generator",   report_generator_node)

    # -------------------------------------------------------------------------
    # Define edges (the execution flow)
    # -------------------------------------------------------------------------

    # Entry point
    graph.set_entry_point("validate_input")
    graph.add_edge("validate_input", "detect_annotation")

    # Conditional: run VEP or skip if already annotated
    graph.add_conditional_edges(
        "detect_annotation",
        _should_run_vep,
        {"run_vep": "run_vep", "skip_vep": "prefilter"},
    )
    graph.add_edge("run_vep",      "prefilter")
    graph.add_edge("prefilter",    "phasing")
    graph.add_edge("phasing",      "post_process")
    graph.add_edge("post_process", "run_agents")
    graph.add_edge("run_agents",   "evidence_aggregator")

    # Conditional: BA1 short-circuit skips full debate
    graph.add_conditional_edges(
        "evidence_aggregator",
        _should_run_debate,
        {"run_debate": "pathogenic_advocate", "skip_debate": "final_arbiter"},
    )
    graph.add_edge("pathogenic_advocate", "benign_advocate")
    graph.add_edge("benign_advocate",     "final_arbiter")

    # Clinical actionability (runs after final classification)
    graph.add_edge("final_arbiter",       "clinical_actionability")

    # Conditional: skip HPO NLP if terms already provided
    graph.add_conditional_edges(
        "clinical_actionability",
        _should_run_hpo_nlp,
        {"run_nlp": "hpo_nlp", "skip_nlp": "hpo_matcher"},
    )
    graph.add_edge("hpo_nlp",          "hpo_matcher")
    graph.add_edge("hpo_matcher",      "phenotype_scorer")
    graph.add_edge("phenotype_scorer", "zygosity_filter")
    graph.add_edge("zygosity_filter",  "report_generator")
    graph.add_edge("report_generator", END)

    return graph


# =============================================================================
# Module-level compiled graph — import this in nodes and the API
# =============================================================================

VARIANT_GRAPH = build_variant_graph().compile()

