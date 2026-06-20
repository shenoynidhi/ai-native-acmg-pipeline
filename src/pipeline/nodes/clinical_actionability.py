"""
src/pipeline/nodes/clinical_actionability.py

Clinical Actionability Node — Therapeutic Recommendations

Runs AFTER debate_final_arbiter, BEFORE report_generator.

Role: Query ASCO/NCCN/ESMO/OncoKB/CIViC guidelines to provide therapeutic
recommendations, genetic counseling guidance, and clinical trial matches based
on the variant's ACMG classification.

This node is SEPARATE from ACMG classification to maintain clinical workflow:
  1. Classify the variant (ACMG)
  2. Determine clinical action (therapeutic guidelines)

RAG queries:
  - clinical_actionability: gene + variant + cancer_type + classification

State fields READ:
  final_classification, gene, variant_id, hgvsp, hgvsc, consequence,
  phenotype_terms (optional)

State fields WRITTEN:
  actionability_result: dict
    - therapy_recommendations: list[dict]
    - genetic_counseling_recommendations: str
    - clinical_trial_matches: list[dict]
    - guideline_references: list[str]
    - overall_evidence_level: str
    - summary: str
    - skip_reason: str (if skipped)
"""

import json
import logging
from typing import Optional, List, Dict
from src.utils.logging_config import get_user_friendly_logger

import chromadb
from chromadb.utils import embedding_functions

from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json
from src.config import CHROMADB_DIR

logger = get_user_friendly_logger('actionability')

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
COLLECTION_NAME = "clinical_actionability"


# ---------------------------------------------------------------------------
# Cancer gene list (matches build_actionability_guidelines.py)
# ---------------------------------------------------------------------------

CANCER_GENES = {
    # Breast/Ovarian/Pancreatic
    "BRCA1", "BRCA2", "PALB2", "CHEK2", "ATM", "BARD1", "BRIP1", "RAD51C", "RAD51D",
    "TP53", "PTEN", "STK11", "CDH1", "NF1",

    # Lynch syndrome / Colorectal
    "MLH1", "MSH2", "MSH6", "PMS2", "EPCAM", "APC", "MUTYH", "SMAD4", "BMPR1A",

    # Lung cancer
    "EGFR", "ALK", "ROS1", "BRAF", "KRAS", "NRAS", "MET", "RET", "ERBB2", "HER2",
    "NTRK1", "NTRK2", "NTRK3",

    # Melanoma
    "BRAF", "NRAS", "KIT", "CDKN2A", "BAP1",

    # Prostate
    "HOXB13",

    # Gastric/GI
    "CTNNA1",

    # Glioma
    "IDH1", "IDH2", "ATRX",

    # Targeted therapy genes
    "PIK3CA", "AKT1", "MTOR", "TSC1", "TSC2", "FGFR1", "FGFR2", "FGFR3",
    "PDGFRA", "FLT3", "JAK2", "ABL1", "SRC",

    # Other common cancer genes
    "VHL", "RB1", "SMARCB1", "MAX", "SDHB", "SDHC", "SDHD",
}

HPO_TO_CANCER_TYPE = {
    "HP:0100013": "breast",
    "HP:0002664": "multiple",
    "HP:0002858": "melanoma",
    "HP:0100526": "lung",
    "HP:0002894": "pancreatic",
    "HP:0002895": "hepatocellular",
    "HP:0002896": "colorectal",
    "HP:0100615": "ovarian",
    "HP:0002885": "glioma",
    "HP:0100273": "renal",
    "HP:0002893": "gastric",
    "HP:0030448": "prostate",
}


# ---------------------------------------------------------------------------
# RAG query helpers
# ---------------------------------------------------------------------------

def _get_actionability_collection():
    """Get the clinical_actionability ChromaDB collection."""
    from src.rag.chromadb_client import get_chromadb_client
    client = get_chromadb_client(CHROMADB_DIR)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )
    try:
        return client.get_collection(COLLECTION_NAME, embedding_function=ef)
    except Exception as e:
        logger.error(
            f"Clinical actionability collection '{COLLECTION_NAME}' not found. "
            f"Run: python src/rag/build_actionability_guidelines.py"
        )
        raise


def _query_actionability_guidelines(
    gene: str,
    variant: str,
    classification: str,
    cancer_type: Optional[str] = None,
) -> List[str]:
    """
    Query clinical actionability RAG database.

    Returns relevant therapeutic recommendations from NCCN/ASCO/ESMO/OncoKB/CIViC.
    """
    try:
        col = _get_actionability_collection()

        # Build query based on classification
        query_parts = [gene]

        if variant:
            # Extract amino acid change (e.g., "p.Val600Glu" → "V600E")
            variant_clean = variant.replace("p.", "").replace("Ter", "*")
            query_parts.append(variant_clean)

        if classification in ["Pathogenic", "Likely_Pathogenic"]:
            # For P/LP: focus on therapeutic recommendations
            query_parts.extend([
                "therapeutic recommendation",
                "FDA approved",
                "NCCN Category 1",
                "targeted therapy",
                "clinical trial",
            ])
        else:
            # For VUS/LB/B: focus on genetic counseling
            query_parts.extend([
                "genetic counseling",
                "hereditary risk",
                "family testing",
                "surveillance",
            ])

        if cancer_type:
            query_parts.append(cancer_type)

        query = " ".join(query_parts)

        logger.info(f"RAG query: {query}")

        # Query with gene filter if possible
        where_filter = {"gene": gene} if gene else None

        results = col.query(
            query_texts=[query],
            n_results=15,  # Get more results for actionability
            where=where_filter,
        )

        docs = results["documents"][0] if results["documents"] else []

        # Also query for hereditary syndrome guidelines if germline
        if gene in ["BRCA1", "BRCA2", "MLH1", "MSH2", "MSH6", "PMS2", "TP53"]:
            hereditary_results = col.query(
                query_texts=[f"{gene} hereditary syndrome genetic counseling management"],
                n_results=5,
            )
            if hereditary_results["documents"]:
                docs.extend(hereditary_results["documents"][0])

        # Remove duplicates
        docs = list(dict.fromkeys(docs))

        return docs

    except Exception as e:
        logger.warning(f"RAG query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _is_cancer_gene(gene: str) -> bool:
    """Check if gene is in cancer gene panel."""
    return gene.upper() in CANCER_GENES


def _infer_cancer_type(phenotype_terms: List[str]) -> Optional[str]:
    """Infer cancer type from HPO terms if present."""
    for term in phenotype_terms:
        if term in HPO_TO_CANCER_TYPE:
            return HPO_TO_CANCER_TYPE[term]
    return None


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_actionability_system_prompt(guideline_chunks: List[str]) -> str:
    """Build system prompt for actionability LLM call."""
    guidelines_text = "\n\n---\n\n".join(guideline_chunks) if guideline_chunks else "No actionability guidelines retrieved."

    return f"""You are an oncology genomics specialist providing therapeutic recommendations
based on ASCO, NCCN, ESMO, OncoKB, and CIViC clinical practice guidelines.

Your role:
1. Review the variant's ACMG classification and gene/variant details.
2. Identify FDA-approved targeted therapies for this biomarker (if any).
3. Identify NCCN-recommended therapies (Category 1, 2A, 2B).
4. Identify relevant active clinical trials.
5. Provide genetic counseling recommendations for germline variants.
6. Cite specific guidelines with version numbers (NCCN v.X.YYYY, ASCO 2024, etc.).

Evidence hierarchy:
- **FDA_Level1**: FDA-approved therapy + FDA-recognized biomarker
- **NCCN_Category1**: NCCN recommended standard of care (highest evidence)
- **NCCN_Category2A**: NCCN recommended based on lower-level evidence
- **Clinical_Trial**: Evidence from clinical trials (not yet standard of care)
- **Preclinical**: Biological evidence only (no clinical data)

Important rules:
- Do NOT recommend therapies without guideline support from the retrieved context.
- For germline variants (BRCA1/2, Lynch genes, TP53): Focus heavily on genetic
  counseling, cascade testing, and surveillance recommendations.
- For somatic variants (tumor-only): Focus on targeted therapy and clinical trials.
- For VUS: Do NOT recommend therapy. Focus on genetic counseling and limitations.
- For Benign/Likely_Benign: Note that no action is needed.
- Always cite the specific guideline version and date.
- If a therapy is FDA-approved for this biomarker, mark fda_approved: true.

CLINICAL ACTIONABILITY GUIDELINES (retrieved from NCCN/ASCO/ESMO/OncoKB/CIViC):
{guidelines_text}

Output format — respond with valid JSON only, no markdown:
{{
  "therapy_recommendations": [
    {{
      "drug": "drug name or combination (e.g., 'dabrafenib + trametinib')",
      "indication": "specific cancer type and stage (e.g., 'unresectable or metastatic melanoma with BRAF V600E mutation')",
      "evidence_level": "FDA_Level1|NCCN_Category1|NCCN_Category2A|Clinical_Trial|Preclinical",
      "guideline_source": "specific guideline name and version (e.g., 'NCCN Guidelines Melanoma v3.2024')",
      "fda_approved": true|false,
      "mechanism": "brief description of how this drug targets the variant (e.g., 'BRAF inhibitor targeting V600E mutation')",
      "references": ["PMID:12345678", "NCT01234567"]
    }}
  ],
  "genetic_counseling_recommendations": "For germline pathogenic variants: cascade testing, surveillance, risk-reducing surgery options, family planning. For somatic variants: note this is tumor-specific. For VUS: note limitations and recommend genetic counseling. Empty string if not applicable.",
  "clinical_trial_matches": [
    {{
      "nct_id": "NCT01234567",
      "title": "trial title from guidelines",
      "phase": "Phase 1|Phase 2|Phase 3",
      "status": "Recruiting|Active|Completed"
    }}
  ],
  "guideline_references": ["List of all guideline sources cited, e.g., 'NCCN Melanoma v3.2024', 'ASCO Precision Medicine 2023', 'OncoKB Level 1'"],
  "overall_evidence_level": "FDA_Level1|NCCN_Category1|Clinical_Trial|Preclinical|Insufficient",
  "summary": "2-3 sentence summary of clinical actionability. For P/LP: summarize therapeutic options. For germline: emphasize genetic counseling. For VUS: emphasize uncertainty."
}}"""


def _build_actionability_user_prompt(state: VariantState, cancer_type: Optional[str]) -> str:
    """Build user prompt for actionability LLM call."""
    variant_id = state.get("variant_id", "?")
    gene = state.get("gene", "?")
    hgvsp = state.get("hgvsp", "")
    hgvsc = state.get("hgvsc", "")
    classification = state.get("final_classification", "VUS")
    consequence = state.get("consequence", "")

    cancer_context = f"Cancer type: {cancer_type}" if cancer_type else "Cancer type: Not specified from phenotype data"

    return f"""VARIANT: {variant_id}
Gene: {gene}
HGVSp: {hgvsp}
HGVSc: {hgvsc}
Consequence: {consequence}
ACMG Classification: {classification}
{cancer_context}

Provide therapeutic recommendations and genetic counseling guidance based on
ASCO/NCCN/ESMO/OncoKB/CIViC guidelines retrieved above.

Focus on:
1. FDA-approved targeted therapies (if any)
2. NCCN-recommended therapies with category
3. Relevant clinical trials
4. Genetic counseling recommendations (especially for germline P/LP variants)

Respond in the required JSON format."""


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

def clinical_actionability_node(state: VariantState) -> dict:
    """
    Clinical actionability analysis node.

    Provides therapeutic recommendations based on ASCO/NCCN/ESMO guidelines.
    """
    variant_id = state.get("variant_id", "?")
    gene = state.get("gene", "unknown")
    classification = state.get("final_classification", "VUS")
    hgvsp = state.get("hgvsp", "")
    hgvsc = state.get("hgvsc", "")

    logger.info(
        f"Processing {variant_id} ({gene}): classification={classification}"
    )

    # Extract cancer type from phenotype if available
    phenotype_terms = state.get("phenotype_terms", [])
    cancer_type = _infer_cancer_type(phenotype_terms)

    # Skip actionability for non-cancer genes
    if not _is_cancer_gene(gene):
        logger.info(f"{gene} not in cancer gene list - skipping actionability")
        return {
            "actionability_result": {
                "skip_reason": f"Gene {gene} is not in the cancer gene panel. Clinical actionability analysis is limited to cancer-related genes.",
                "therapy_recommendations": [],
                "genetic_counseling_recommendations": "",
                "clinical_trial_matches": [],
                "guideline_references": [],
                "overall_evidence_level": "Not_Applicable",
                "summary": f"Actionability analysis not applicable for {gene} (non-cancer gene).",
            }
        }

    # Skip actionability for benign variants
    if classification in ["Benign", "Likely_Benign"]:
        logger.info(f"{variant_id} classified as {classification} - skipping actionability")
        return {
            "actionability_result": {
                "skip_reason": f"Variant classified as {classification}. No clinical action required.",
                "therapy_recommendations": [],
                "genetic_counseling_recommendations": "",
                "clinical_trial_matches": [],
                "guideline_references": [],
                "overall_evidence_level": "Not_Applicable",
                "summary": f"No clinical action needed for {classification} variants.",
            }
        }

    # Query RAG for actionability guidelines
    try:
        guideline_chunks = _query_actionability_guidelines(
            gene, hgvsp or hgvsc, classification, cancer_type
        )

        logger.info(f"Retrieved {len(guideline_chunks)} guideline chunks")

        if not guideline_chunks:
            logger.warning(f"No actionability guidelines found for {gene} {hgvsp or hgvsc}")
            return {
                "actionability_result": {
                    "skip_reason": f"No actionability guidelines available for {gene} variant {hgvsp or hgvsc}.",
                    "therapy_recommendations": [],
                    "genetic_counseling_recommendations": _get_generic_counseling_rec(gene, classification),
                    "clinical_trial_matches": [],
                    "guideline_references": [],
                    "overall_evidence_level": "Insufficient",
                    "summary": f"No established therapeutic guidelines for this {gene} variant. Consider genetic counseling.",
                }
            }

    except Exception as e:
        logger.error(f"Failed to query actionability database: {e}")
        return {
            "actionability_result": {
                "skip_reason": f"Clinical actionability database unavailable. Error: {str(e)}",
                "therapy_recommendations": [],
                "genetic_counseling_recommendations": "",
                "clinical_trial_matches": [],
                "guideline_references": [],
                "overall_evidence_level": "Error",
                "summary": "Actionability analysis failed due to database error.",
            }
        }

    # Build prompts
    system_prompt = _build_actionability_system_prompt(guideline_chunks)
    user_prompt = _build_actionability_user_prompt(state, cancer_type)

    # Call LLM
    try:
        result = call_llm_json(system_prompt, user_prompt)

        logger.info(
            f"{variant_id}: Found {len(result.get('therapy_recommendations', []))} therapeutic options, "
            f"evidence level: {result.get('overall_evidence_level', 'unknown')}"
        )

        return {
            "actionability_result": result
        }

    except Exception as e:
        logger.error(f"LLM call failed for actionability: {e}")
        return {
            "actionability_result": {
                "skip_reason": f"Failed to generate actionability recommendations: {str(e)}",
                "therapy_recommendations": [],
                "genetic_counseling_recommendations": _get_generic_counseling_rec(gene, classification),
                "clinical_trial_matches": [],
                "guideline_references": [],
                "overall_evidence_level": "Error",
                "summary": "Actionability analysis incomplete due to processing error.",
            }
        }


def _get_generic_counseling_rec(gene: str, classification: str) -> str:
    """Provide generic genetic counseling recommendation as fallback."""
    if classification in ["Pathogenic", "Likely_Pathogenic"]:
        if gene in ["BRCA1", "BRCA2"]:
            return (
                "This is a pathogenic variant in a hereditary cancer gene. "
                "Genetic counseling is strongly recommended for cascade testing, "
                "enhanced surveillance, and risk-reducing interventions."
            )
        elif gene in ["MLH1", "MSH2", "MSH6", "PMS2"]:
            return (
                "This is a pathogenic variant in a Lynch syndrome gene. "
                "Genetic counseling is strongly recommended for cascade testing "
                "and enhanced colorectal/endometrial cancer surveillance."
            )
        else:
            return (
                "Genetic counseling is recommended to discuss implications "
                "for the patient and family members."
            )
    elif classification == "VUS":
        return (
            "This is a variant of uncertain significance. Genetic counseling "
            "is recommended to discuss limitations and potential for reclassification."
        )
    else:
        return ""

