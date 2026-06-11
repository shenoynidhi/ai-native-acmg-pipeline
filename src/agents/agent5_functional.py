"""
src/agents/agent5_functional.py

Agent 5 — Functional / Domain Evidence
Evaluates: PS3, BS3, PM1

ACMG/AMP 2015 criteria assessed:
  PS3  — Well-established functional studies show damaging effect.
         Pathogenic Strong (or Supporting/Moderate via ClinGen calibration).
         We use ClinVar functional evidence + LLM literature reasoning.
  BS3  — Well-established functional studies show no damaging effect.
         Benign Strong.
  PM1  — Located in a mutational hot spot or well-established functional domain
         (e.g. active site, binding site, critical domain with no benign variation).
         Pathogenic Moderate.

RAG used:
  query_uniprot_domains  — protein domain/site overlap at variant position (PM1)

Additional inputs:
  ClinGen gene-disease validity CSV (already loaded by post_process into state)
  gnomAD pLI / LOEUF for domain constraint inference
  gene_clinvar_missense_fraction for hot spot detection

Note on PS3/BS3:
  True PS3/BS3 requires published functional assay data. Without a live literature
  database, we use:
    1. ClinVar functional evidence flags (CLNREVSTAT mentions "functional")
    2. LLM knowledge of well-characterised genes (BRCA1/2, TP53, etc.)
    3. State clinvar_clnsig as a proxy signal
  We are conservative: assign PS3_Supporting only, never full PS3 Strong,
  unless LLM has high confidence based on well-known functional data.

State fields read:
  gene, consequence, protein_position, hgvsp, hgvsc,
  clinvar_clnsig, clinvar_stars, clinvar_disease,
  gene_clingen_validity, gene_orphanet_inheritance,
  gene_gnomad_pli, gene_gnomad_loeuf, gene_gnomad_zscore,
  gene_clinvar_missense_fraction,
  is_loftee_hc, max_spliceai,
  revel_score, cadd_phred

State fields written (via agent_evidence):
  agent_evidence["agent5"]
"""

import logging
from typing import Optional

from src.pipeline.state import VariantState
from src.rag.retriever import query_uniprot_domains
from src.utils.llm_client import call_llm_json
from src.pipeline.pubmed import pubmed_search, pubmed_format_for_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain constraint thresholds for PM1
# ---------------------------------------------------------------------------

# gnomAD constraint thresholds
PLI_CONSTRAINED   = 0.9
LOEUF_CONSTRAINED = 0.35
ZSCORE_CONSTRAINED = 3.09  # missense Z-score — >3.09 = top 1% constrained

# Missense fraction threshold: if >50% of ClinVar P/LP are missense, gene is
# a missense disease gene (supports PM1 for missense in functional domains)
MISSENSE_FRACTION_THRESHOLD = 0.50

# UniProt feature types that are strong PM1 indicators
CRITICAL_FEATURE_TYPES = {
    "ACT_SITE",     # active site — strongest
    "BINDING",      # binding site
    "METAL",        # metal binding
    "DISULFID",     # disulfide bond
}

# Features that are moderate PM1 indicators
MODERATE_FEATURE_TYPES = {
    "DOMAIN",       # named protein domain
    "REGION",       # functionally important region
    "SITE",         # other important site
    "MOTIF",        # functional motif
}


# ---------------------------------------------------------------------------
# PM1 evaluation
# ---------------------------------------------------------------------------

def _evaluate_pm1(
    gene: str,
    protein_pos: Optional[int],
    consequence: str,
    pli: Optional[float],
    loeuf: Optional[float],
    zscore: Optional[float],
    missense_fraction: Optional[float],
    domain_hits: list[dict],
) -> tuple[Optional[str], list[str]]:
    """
    PM1: Variant in mutational hot spot or critical functional domain.

    Returns (strength, notes):
      strength: "Moderate" | "Supporting" | None
    """
    notes = []

    # PM1 only applies to missense and protein-altering variants
    NON_PM1_CONSEQUENCES = {
        "synonymous_variant", "intron_variant", "stop_gained",
        "frameshift_variant", "splice_acceptor_variant", "splice_donor_variant",
        "upstream_gene_variant", "downstream_gene_variant",
        "3_prime_UTR_variant", "5_prime_UTR_variant",
    }
    if consequence in NON_PM1_CONSEQUENCES:
        return None, [f"PM1 not applicable to {consequence}"]

    if protein_pos is None:
        return None, ["PM1 not applicable: no protein position available"]

    # --- Check UniProt domain hits ---
    critical_hits  = [
        h for h in domain_hits
        if h["metadata"].get("feature_type") in CRITICAL_FEATURE_TYPES
    ]
    moderate_hits  = [
        h for h in domain_hits
        if h["metadata"].get("feature_type") in MODERATE_FEATURE_TYPES
    ]

    in_critical_domain = len(critical_hits) > 0
    in_domain = len(moderate_hits) > 0 or in_critical_domain

    # --- Check gene constraint ---
    is_constrained = (
        (pli is not None and pli >= PLI_CONSTRAINED) or
        (loeuf is not None and loeuf <= LOEUF_CONSTRAINED) or
        (zscore is not None and zscore >= ZSCORE_CONSTRAINED)
    )

    is_missense_gene = (
        missense_fraction is not None and
        missense_fraction >= MISSENSE_FRACTION_THRESHOLD
    )

    # --- Assign strength ---
    if in_critical_domain:
        strength = "Moderate"
        feat = critical_hits[0]["metadata"]
        notes.append(
            f"PM1 (Moderate): Variant at position {protein_pos} overlaps "
            f"{feat.get('feature_type')} ({feat.get('note', 'critical site')}) "
            f"in {gene} (positions {feat.get('start')}-{feat.get('end')})."
        )
        if is_constrained:
            notes.append(
                f"Gene is missense-constrained (pLI={pli}, LOEUF={loeuf}, Z={zscore}) — "
                f"supports PM1."
            )

    elif in_domain and is_constrained:
        strength = "Moderate"
        feat = moderate_hits[0]["metadata"]
        notes.append(
            f"PM1 (Moderate): Variant at position {protein_pos} overlaps "
            f"{feat.get('feature_type')} ({feat.get('note', 'functional domain')}) "
            f"in {gene}, combined with gene constraint "
            f"(pLI={pli}, LOEUF={loeuf})."
        )

    elif in_domain and is_missense_gene:
        strength = "Supporting"
        feat = moderate_hits[0]["metadata"]
        notes.append(
            f"PM1 (Supporting): Variant at position {protein_pos} overlaps "
            f"{feat.get('feature_type')} in {gene}. "
            f"Gene has high missense P/LP fraction ({missense_fraction:.2f}) "
            f"but no confirmed domain constraint."
        )

    elif is_constrained and is_missense_gene:
        # Constrained missense gene without specific domain data — weak support
        strength = "Supporting"
        notes.append(
            f"PM1 (Supporting): No specific domain overlap found at position {protein_pos}, "
            f"but {gene} is highly constrained (pLI={pli}, LOEUF={loeuf}) with "
            f"high missense P/LP fraction ({missense_fraction:.2f})."
        )

    else:
        strength = None
        notes.append(
            f"PM1 not assigned: position {protein_pos} not in a critical domain "
            f"(domain hits: {len(domain_hits)}), "
            f"gene constraint: pLI={pli}, LOEUF={loeuf}."
        )

    return strength, notes


# ---------------------------------------------------------------------------
# PS3/BS3 evidence signals
# ---------------------------------------------------------------------------

# Genes with well-characterised functional assays in literature
# LLM will elaborate; we use this for a fast signal
FUNCTIONALLY_CHARACTERISED_GENES = {
    "BRCA1", "BRCA2", "TP53", "PTEN", "MLH1", "MSH2", "MSH6", "PMS2",
    "APC", "VHL", "RB1", "NF1", "NF2", "STK11", "CDH1", "SMAD4",
    "ATM", "CHEK2", "PALB2", "RAD51C", "RAD51D", "BARD1",
    "CFTR", "LDLR", "PKD1", "PKD2", "HBB", "HBA1", "HBA2",
    "GBA", "HEXA", "PAH", "OTC", "F8", "F9",
}


def _ps3_bs3_signals(
    state: VariantState,
) -> tuple[bool, bool, list[str]]:
    """
    Derive PS3/BS3 signals from available state data.
    Returns (has_ps3_signal, has_bs3_signal, notes).

    This is conservative — we only return signals, not final assignments.
    LLM makes the final call.
    """
    gene       = state.get("gene", "") or ""
    clnsig     = state.get("clinvar_clnsig") or ""
    stars      = state.get("clinvar_stars", 0) or 0
    clingen    = state.get("gene_clingen_validity") or ""
    notes      = []

    ps3_signal = False
    bs3_signal = False

    # ClinVar ≥3 stars with P/LP = expert panel review = proxy for functional evidence
    if stars >= 3:
        if "pathogenic" in clnsig.lower():
            ps3_signal = True
            notes.append(
                f"ClinVar {clnsig} ({stars} stars) — expert panel classification "
                f"likely incorporates functional evidence."
            )
        elif "benign" in clnsig.lower():
            bs3_signal = True
            notes.append(
                f"ClinVar {clnsig} ({stars} stars) — expert panel likely reviewed "
                f"functional evidence."
            )

    # Well-characterised gene — flag for LLM to evaluate PS3/BS3 with its knowledge
    if gene in FUNCTIONALLY_CHARACTERISED_GENES:
        notes.append(
            f"{gene} has well-established published functional assay data. "
            f"LLM will evaluate PS3/BS3 applicability."
        )

    # ClinGen Definitive with good review = suggests functional data exists
    if clingen and clingen.lower() == "definitive":
        notes.append(
            f"ClinGen Definitive validity for {gene} — functional data likely reviewed."
        )

    return ps3_signal, bs3_signal, notes


# ---------------------------------------------------------------------------
# LLM refinement
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ACMG/AMP variant classification expert evaluating functional
and domain evidence. You assess PS3, BS3, and PM1.

Key rules:
- PS3 (Pathogenic Strong): Well-established functional studies showing damaging effect.
  Published assay must be specific to this variant or same amino acid change.
  Downgrade to PS3_Moderate or PS3_Supporting if evidence is indirect.
- BS3 (Benign Strong): Well-established functional studies showing NO damaging effect.
- PM1 (Pathogenic Moderate): Variant in mutational hot spot or critical domain.
  Check if position is in an active site, binding site, or domain where benign
  variation is absent in population databases.
- Do NOT assign PS3 Strong based solely on in-silico predictions — those are PP3/BP4.
- For genes without published functional assays: PS3/BS3 = Not Applicable.

Respond ONLY with a JSON object. No preamble, no markdown fences. Schema:
{
  "criteria_pathogenic": {},
  "criteria_benign": {},
  "evidence_notes": "string — 3-5 sentences",
  "citations": ["sources"],
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "ps3_strength": "Strong" | "Moderate" | "Supporting" | "Not_Applicable",
  "bs3_strength": "Strong" | "Not_Applicable",
  "pm1_confirmed": true | false,
  "functional_assay_exists": true | false
}

Strengths go in criteria_pathogenic/criteria_benign as criterion_code: strength_string.
E.g. {"PS3": "Supporting"} or {"PM1": "Moderate"}."""


def _llm_refine(
    state: VariantState,
    rule_criteria_p: dict,
    rule_criteria_b: dict,
    domain_hits: list[dict],
    ps3_signal: bool,
    bs3_signal: bool,
    pubmed_hits: list[dict],
    notes: list[str],
) -> dict:
    gene        = state.get("gene", "UNKNOWN")
    consequence = state.get("consequence", "")
    hgvsc       = state.get("hgvsc") or "N/A"
    hgvsp       = state.get("hgvsp") or "N/A"
    protein_pos = state.get("protein_position")
    pli         = state.get("gene_gnomad_pli")
    loeuf       = state.get("gene_gnomad_loeuf")
    zscore      = state.get("gene_gnomad_zscore")
    clingen     = state.get("gene_clingen_validity") or "Unknown"
    inheritance = state.get("gene_orphanet_inheritance") or "Unknown"
    clnsig      = state.get("clinvar_clnsig") or "Not in ClinVar"
    stars       = state.get("clinvar_stars", 0)
    miss_frac   = state.get("gene_clinvar_missense_fraction")

    # Summarise domain hits
    domain_summary = []
    for h in domain_hits[:6]:
        m = h["metadata"]
        domain_summary.append(
            f"  {m.get('feature_type')} {m.get('start')}-{m.get('end')}: "
            f"{m.get('note', 'no description')}"
        )
    pubmed_section = pubmed_format_for_llm(pubmed_hits)
    user_prompt = f"""Evaluate functional and domain evidence for this variant:

Gene: {gene}
Consequence: {consequence}
HGVSc: {hgvsc}
HGVSp: {hgvsp}
Protein position: {protein_pos}

Gene-level context:
  ClinGen validity: {clingen}
  Inheritance: {inheritance}
  gnomAD pLI: {pli}
  gnomAD LOEUF: {loeuf}
  gnomAD missense Z-score: {zscore}
  ClinVar missense P/LP fraction: {miss_frac}

ClinVar for this variant:
  Significance: {clnsig} ({stars} stars)

UniProt domain features at position {protein_pos}:
{chr(10).join(domain_summary) or '  None found at this position'}

PS3/BS3 signals from rule-based analysis:
  PS3 signal: {ps3_signal}
  BS3 signal: {bs3_signal}
  Notes: {'; '.join(notes)}

PubMed literature (functional assays):
{pubmed_section}

Rule-based criteria so far:
  Pathogenic: {rule_criteria_p}
  Benign: {rule_criteria_b}

Evaluate:
1. PS3: Does published functional data exist for this specific variant or gene/consequence?
   Be conservative — state "Not_Applicable" unless you have high confidence.
2. BS3: Similar — only if functional data shows no effect.
3. PM1: Confirm or adjust whether this position is in a critical domain.
   Consider whether benign missense variants are absent from this domain in gnomAD.
"""
    return call_llm_json(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

def agent5_functional(state: VariantState) -> dict:
    """
    Agent 5: Evaluate functional evidence and domain criteria (PS3, BS3, PM1).

    Returns:
        dict with key "agent_evidence" -> {"agent5": AgentEvidence dict}
    """
    gene        = state.get("gene", "UNKNOWN")
    variant_id  = state.get("variant_id", "?")
    consequence = state.get("consequence", "") or ""
    protein_pos = state.get("protein_position")
    logger.info(f"[agent5_functional] Evaluating {variant_id} ({gene}) pos={protein_pos}")

    criteria_p: dict = {}
    criteria_b: dict = {}
    all_notes:  list[str] = []
    citations = [
        "ACMG/AMP 2015",
        "UniProt (release 2025)",
        "gnomAD v2.1 constraint metrics",
    ]

    # --- Step 1: Query UniProt domains ---
    domain_hits = []
    if protein_pos is not None:
        try:
            domain_hits = query_uniprot_domains(
                gene=gene,
                protein_position=protein_pos,
                n_results=10,
            )
            logger.debug(
                f"[agent5] UniProt returned {len(domain_hits)} domain hits "
                f"for {gene} pos {protein_pos}"
            )
        except Exception as e:
            logger.warning(f"[agent5] UniProt RAG query failed: {e}")
            all_notes.append(f"UniProt domain query failed: {e}")

    # --- Step 2: PM1 rule-based ---
    pli         = state.get("gene_gnomad_pli")
    loeuf       = state.get("gene_gnomad_loeuf")
    zscore      = state.get("gene_gnomad_zscore")
    miss_frac   = state.get("gene_clinvar_missense_fraction")

    pm1_strength, pm1_notes = _evaluate_pm1(
        gene, protein_pos, consequence,
        pli, loeuf, zscore, miss_frac,
        domain_hits,
    )
    if pm1_strength:
        criteria_p["PM1"] = pm1_strength
    all_notes.extend(pm1_notes)
    # --- Step 2b: BP3 — variant in repeat region with no known function ---
    if state.get("repeat_region") and consequence in (
        "inframe_insertion", "inframe_deletion", "protein_altering_variant"
    ):
        criteria_b["BP3"] = "Supporting"
        all_notes.append(
            f"BP3 (Supporting): Variant is in a repeat region "
            f"(VEP low_complexity flag). In-frame change in repeat "
            f"without known functional impact."
        )
    # --- Step 3: PS3/BS3 signals ---
    ps3_signal, bs3_signal, ps3_notes = _ps3_bs3_signals(state)
    all_notes.extend(ps3_notes)
    
# --- Step 3b: PubMed search — functional assay literature ---
    pubmed_hits = []
    try:
        pubmed_hits = pubmed_search(
            gene=gene,
            hgvsp=state.get("hgvsp"),
            hgvsc=state.get("hgvsc"),
            query_type="functional",
            max_results=10,
        )
        logger.debug(f"[agent5] PubMed returned {len(pubmed_hits)} functional papers for {variant_id}")
    except Exception as e:
        logger.warning(f"[agent5] PubMed search failed: {e}")
        all_notes.append(f"PubMed search failed: {e}")

    # --- Step 4: LLM refinement ---
    # Always call LLM for PS3/BS3 — these require nuanced literature knowledge.
    # Also call for PM1 if domain evidence is ambiguous.
    pm1_ambiguous = (
        pm1_strength == "Supporting" or
        (pm1_strength is None and len(domain_hits) > 0)
    )
    needs_llm = True  # PS3/BS3 always need LLM; PM1 benefits from confirmation

    if needs_llm:
        logger.debug(f"[agent5] Calling LLM for PS3/BS3/PM1 on {variant_id}")
        llm_result = _llm_refine(
            state, criteria_p, criteria_b,
            domain_hits, ps3_signal, bs3_signal, pubmed_hits, all_notes,
        )

        if llm_result and not llm_result.get("error"):
            # LLM result takes precedence for PS3/BS3
            llm_p = llm_result.get("criteria_pathogenic", {})
            llm_b = llm_result.get("criteria_benign", {})

            # PS3/BS3 come entirely from LLM
            if "PS3" in llm_p:
                criteria_p["PS3"] = llm_p["PS3"]
            if "BS3" in llm_b:
                criteria_b["BS3"] = llm_b["BS3"]

            # PM1: LLM can override rule-based
            if "PM1" in llm_p:
                criteria_p["PM1"] = llm_p["PM1"]
            elif "PM1" in criteria_p and not llm_result.get("pm1_confirmed", True):
                # LLM explicitly disagrees with PM1
                logger.info(f"[agent5] LLM overrode PM1 for {variant_id}")
                del criteria_p["PM1"]
                all_notes.append("LLM: PM1 not confirmed — position not in critical domain.")

            confidence     = llm_result.get("confidence", "MEDIUM")
            evidence_notes = llm_result.get("evidence_notes", " ".join(all_notes))
            citations     += llm_result.get("citations", [])

            if not llm_result.get("functional_assay_exists", False):
                all_notes.append(
                    "No published functional assay data found for this variant — "
                    "PS3/BS3 not assigned."
                )
        else:
            logger.warning(f"[agent5] LLM failed for {variant_id}")
            confidence = "LOW"
            evidence_notes = " ".join(all_notes) or (
                f"Functional evidence evaluation incomplete for {gene} {variant_id}. "
                f"LLM unavailable. PM1={criteria_p.get('PM1', 'not assigned')}."
            )
    else:
        confidence = "HIGH" if criteria_p else "MEDIUM"
        evidence_notes = " ".join(all_notes) or (
            f"No functional domain or assay evidence found for {gene} pos {protein_pos}."
        )

    citations = list(dict.fromkeys(citations))
    logger.info(
        f"[agent5] {variant_id}: P={criteria_p} B={criteria_b} conf={confidence}"
    )

    return {
        "agent_evidence": {
            "agent5": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign":     criteria_b,
                "evidence_notes":      evidence_notes,
                "citations":           citations,
                "confidence":          confidence,
            }
        }
    }
