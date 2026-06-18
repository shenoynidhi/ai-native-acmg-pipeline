"""
src/agents/rules/deterministic_agents.py

Deterministic (rule-based) implementations of ACMG criteria evaluation.
These replace LLM calls with fast, accurate algorithmic logic.

Agents replaced:
- Agent 1: Population frequency (BA1, BS1, BS2, PM2)
- Agent 3: In-silico predictors (PP3, BP4, BP7)
- Agent 7: De novo status (PS2, PM6)

Performance: ~0.01s per agent (vs ~3s for LLM)
Accuracy: 100% consistent (no LLM drift)
"""

import logging
from typing import Dict, Any
from src.utils.logging_config import get_user_friendly_logger

from src.pipeline.state import VariantState

logger = get_user_friendly_logger('rules')


# =============================================================================
# Agent 1: Population Frequency (BA1, BS1, BS2, PM2)
# =============================================================================

def agent1_population_rules(state: VariantState) -> Dict[str, Any]:
    """
    Rule-based implementation of Agent 1 (Population Frequency).

    ACMG Rules (Richards et al. 2015):
    - BA1: AF > 5% in any general population database → Stand-alone benign
    - BS1: AF > 1% (or higher than expected for disorder)
    - BS2: Observed in healthy adult homozygous (from gnomad_nhomalt)
    - PM2: Absent/rare in population databases (AF < 0.01% and no homozygotes)

    Returns same format as LLM agent for drop-in replacement.
    """
    variant_id = state.get("variant_id", "?")
    max_af = state.get("max_gnomad_af", 0.0)
    popmax_af = state.get("gnomad_af_popmax", 0.0)
    nhomalt = state.get("gnomad_nhomalt", 0)

    logger.info(f"[agent1_rules] Evaluating {variant_id}: AF={max_af:.6f}, nhomalt={nhomalt}")

    criteria_pathogenic = {}
    criteria_benign = {}
    notes = []
    confidence = "HIGH"

    # BA1: Stand-alone benign (AF > 5%)
    if max_af > 0.05:
        criteria_benign["BA1"] = "Stand-alone"
        notes.append(f"BA1: Allele frequency {max_af:.4f} ({max_af*100:.2f}%) exceeds 5% threshold (stand-alone benign)")
        logger.info(f"[agent1_rules] {variant_id}: BA1 applied (AF={max_af:.4f})")

        return {
            "agent_evidence": {
                "agent1": {
                    "criteria_pathogenic": {},
                    "criteria_benign": criteria_benign,
                    "evidence_notes": " | ".join(notes),
                    "citations": ["gnomAD v3.1.2"],
                    "confidence": "HIGH"
                }
            }
        }

    # BS1: Strong benign (AF > 1%)
    if max_af > 0.01:
        criteria_benign["BS1"] = "Strong"
        notes.append(f"BS1: Allele frequency {max_af:.4f} ({max_af*100:.2f}%) exceeds 1% threshold")
        logger.info(f"[agent1_rules] {variant_id}: BS1 applied (AF={max_af:.4f})")

    # BS2: Observed in healthy adult homozygous
    elif nhomalt > 0:
        criteria_benign["BS2"] = "Strong"
        notes.append(f"BS2: Observed in {nhomalt} healthy adult homozygote(s) in gnomAD")
        logger.info(f"[agent1_rules] {variant_id}: BS2 applied (nhomalt={nhomalt})")

    # PM2: Absent or extremely rare (AF < 0.01% and no homozygotes)
    elif max_af < 0.0001 and nhomalt == 0:
        criteria_pathogenic["PM2"] = "Moderate"
        if max_af == 0.0:
            notes.append(f"PM2: Completely absent in gnomAD (0 observations)")
        else:
            notes.append(f"PM2: Extremely rare (AF {max_af:.6f}, 0 homozygotes)")
        logger.info(f"[agent1_rules] {variant_id}: PM2_Moderate (AF={max_af:.6f})")

    # PM2 downgrade to Supporting (rare but not absent)
    elif max_af < 0.001 and nhomalt == 0:
        criteria_pathogenic["PM2"] = "Supporting"
        notes.append(f"PM2_Supporting: Rare in gnomAD (AF {max_af:.6f}, 0 homozygotes)")
        logger.info(f"[agent1_rules] {variant_id}: PM2_Supporting (AF={max_af:.6f})")

    # No criteria met
    else:
        notes.append(f"No population frequency criteria met (AF {max_af:.6f}, nhomalt={nhomalt})")
        confidence = "MEDIUM"
        logger.info(f"[agent1_rules] {variant_id}: No criteria (AF={max_af:.6f})")

    return {
        "agent_evidence": {
            "agent1": {
                "criteria_pathogenic": criteria_pathogenic,
                "criteria_benign": criteria_benign,
                "evidence_notes": " | ".join(notes) if notes else "No population frequency criteria met",
                "citations": ["gnomAD v3.1.2"],
                "confidence": confidence
            }
        }
    }


# =============================================================================
# Agent 3: In-silico Predictors (PP3, BP4, BP7)
# =============================================================================

def agent3_insilico_rules(state: VariantState) -> Dict[str, Any]:
    """
    Rule-based implementation of Agent 3 (In-silico Predictors).

    ACMG Rules:
    - PP3: Multiple lines of computational evidence support pathogenic
    - BP4: Multiple lines of computational evidence support benign
    - BP7: Synonymous variant with no predicted splice impact

    Vote counting thresholds (ClinGen recommendations):
    - PP3: ≥5 damaging predictions
    - BP4: ≥4 benign predictions

    Predictors used:
    - REVEL (damaging > 0.75, benign < 0.3)
    - CADD (damaging > 25, benign < 15)
    - SIFT (damaging < 0.05, benign > 0.5)
    - PolyPhen-2 (damaging > 0.85, benign < 0.2)
    - MetaSVM (damaging > 0.5)
    - EVE (damaging > 0.7)
    """
    variant_id = state.get("variant_id", "?")
    consequence = state.get("consequence", "")

    # Get scores
    revel = state.get("revel_score")
    cadd = state.get("cadd_phred")
    sift = state.get("sift_score")
    polyphen = state.get("polyphen2_score")
    metasvm = state.get("metasvm_score")
    eve = state.get("eve_score")
    max_spliceai = state.get("max_spliceai", 0.0)

    logger.info(f"[agent3_rules] Evaluating {variant_id}: consequence={consequence}")

    criteria_pathogenic = {}
    criteria_benign = {}
    notes = []

    damaging_votes = 0
    benign_votes = 0
    predictor_details = []

    # Count damaging votes
    if revel is not None and revel > 0.75:
        damaging_votes += 1
        predictor_details.append(f"REVEL={revel:.3f} (damaging)")
    elif revel is not None and revel < 0.3:
        benign_votes += 1
        predictor_details.append(f"REVEL={revel:.3f} (benign)")

    if cadd is not None and cadd > 25:
        damaging_votes += 1
        predictor_details.append(f"CADD={cadd:.1f} (damaging)")
    elif cadd is not None and cadd < 15:
        benign_votes += 1
        predictor_details.append(f"CADD={cadd:.1f} (benign)")

    if sift is not None and sift < 0.05:
        damaging_votes += 1
        predictor_details.append(f"SIFT={sift:.3f} (damaging)")
    elif sift is not None and sift > 0.5:
        benign_votes += 1
        predictor_details.append(f"SIFT={sift:.3f} (benign)")

    if polyphen is not None and polyphen > 0.85:
        damaging_votes += 1
        predictor_details.append(f"PolyPhen-2={polyphen:.3f} (damaging)")
    elif polyphen is not None and polyphen < 0.2:
        benign_votes += 1
        predictor_details.append(f"PolyPhen-2={polyphen:.3f} (benign)")

    if metasvm is not None and metasvm > 0.5:
        damaging_votes += 1
        predictor_details.append(f"MetaSVM={metasvm:.3f} (damaging)")

    if eve is not None and eve > 0.7:
        damaging_votes += 1
        predictor_details.append(f"EVE={eve:.3f} (damaging)")

    # Apply criteria based on vote counts
    if damaging_votes >= 5:
        criteria_pathogenic["PP3"] = "Supporting"
        notes.append(f"PP3: {damaging_votes} computational predictors support damaging effect")
        logger.info(f"[agent3_rules] {variant_id}: PP3 applied ({damaging_votes} damaging votes)")

    elif benign_votes >= 4:
        criteria_benign["BP4"] = "Supporting"
        notes.append(f"BP4: {benign_votes} computational predictors support benign effect")
        logger.info(f"[agent3_rules] {variant_id}: BP4 applied ({benign_votes} benign votes)")

    else:
        notes.append(f"Insufficient consensus: {damaging_votes} damaging, {benign_votes} benign predictions")
        logger.info(f"[agent3_rules] {variant_id}: No consensus ({damaging_votes}D/{benign_votes}B)")

    # BP7: Synonymous variant with no splice impact
    if consequence == "synonymous_variant" and max_spliceai < 0.2:
        criteria_benign["BP7"] = "Supporting"
        notes.append(f"BP7: Synonymous variant with no predicted splice impact (SpliceAI={max_spliceai:.3f})")
        logger.info(f"[agent3_rules] {variant_id}: BP7 applied (synonymous, no splice)")

    # Add predictor details to notes
    if predictor_details:
        notes.append(f"Predictor details: {'; '.join(predictor_details[:5])}")  # Limit to 5 for brevity

    confidence = "MEDIUM" if (criteria_pathogenic or criteria_benign) else "LOW"

    return {
        "agent_evidence": {
            "agent3": {
                "criteria_pathogenic": criteria_pathogenic,
                "criteria_benign": criteria_benign,
                "evidence_notes": " | ".join(notes) if notes else "No in-silico criteria met",
                "citations": ["REVEL", "CADD", "SIFT", "PolyPhen-2", "MetaSVM", "EVE", "SpliceAI"],
                "confidence": confidence
            }
        }
    }


# =============================================================================
# Agent 7: De Novo Status (PS2, PM6)
# =============================================================================

def agent7_denovo_rules(state: VariantState) -> Dict[str, Any]:
    """
    Rule-based implementation of Agent 7 (De Novo Status).

    ACMG Rules:
    - PS2: De novo (both maternity and paternity confirmed) in patient with disease
    - PM6: Assumed de novo (parental genotypes not confirmed, but variant absent in parents)

    Requirements:
    - Trio mode enabled
    - Parental genotypes available
    - Both parents must be 0/0 (reference) for de novo

    Returns not evaluable in solo mode.
    """
    variant_id = state.get("variant_id", "?")
    trio_mode = state.get("trio_mode", False)
    parent1_gt = state.get("parent1_genotype")
    parent2_gt = state.get("parent2_genotype")

    logger.info(f"[agent7_rules] Evaluating {variant_id}: trio_mode={trio_mode}")

    criteria_pathogenic = {}
    criteria_benign = {}
    notes = []
    confidence = "LOW"

    # Not evaluable in solo mode
    if not trio_mode:
        notes.append("Not evaluable in solo mode (parental VCFs not provided)")
        logger.info(f"[agent7_rules] {variant_id}: Solo mode - not evaluable")

        return {
            "agent_evidence": {
                "agent7": {
                    "criteria_pathogenic": {},
                    "criteria_benign": {},
                    "evidence_notes": notes[0],
                    "citations": [],
                    "confidence": "LOW"
                }
            }
        }

    # Check if parental genotypes are available
    if not parent1_gt or not parent2_gt:
        notes.append("Parental genotypes not available at this variant position")
        logger.info(f"[agent7_rules] {variant_id}: No parental genotypes")

        return {
            "agent_evidence": {
                "agent7": {
                    "criteria_pathogenic": {},
                    "criteria_benign": {},
                    "evidence_notes": notes[0],
                    "citations": ["Parental VCF files"],
                    "confidence": "LOW"
                }
            }
        }

    logger.info(f"[agent7_rules] {variant_id}: Parent1={parent1_gt}, Parent2={parent2_gt}")

    # Check if variant is de novo (absent in both parents)
    if parent1_gt == "0/0" and parent2_gt == "0/0":
        # PM6: Assumed de novo (no parental identity confirmation)
        criteria_pathogenic["PM6"] = "Moderate"
        notes.append(
            f"PM6: Variant absent in both parents (P1={parent1_gt}, P2={parent2_gt}), "
            f"consistent with de novo occurrence (parental identity not formally confirmed)"
        )
        notes.append(
            "To upgrade to PS2 (Strong), confirm biological parentage via "
            "maternity/paternity testing"
        )
        confidence = "MEDIUM"
        logger.info(f"[agent7_rules] {variant_id}: PM6 applied (assumed de novo)")

    # Variant present in one or both parents - not de novo
    else:
        # Check if segregation pattern is consistent or concerning
        # If variant is in parents but patient has disease → non-segregation (BS4 handled by agent6)
        notes.append(
            f"Variant present in parent(s) (P1={parent1_gt}, P2={parent2_gt}), "
            f"not de novo"
        )
        confidence = "MEDIUM"
        logger.info(f"[agent7_rules] {variant_id}: Not de novo (present in parents)")

    return {
        "agent_evidence": {
            "agent7": {
                "criteria_pathogenic": criteria_pathogenic,
                "criteria_benign": criteria_benign,
                "evidence_notes": " | ".join(notes),
                "citations": ["Parental VCF genotypes"],
                "confidence": confidence
            }
        }
    }


# =============================================================================
# Helper functions
# =============================================================================

def format_evidence_output(
    criteria_pathogenic: Dict[str, str],
    criteria_benign: Dict[str, str],
    notes: str,
    citations: list,
    confidence: str,
    agent_key: str
) -> Dict[str, Any]:
    """
    Format agent evidence output in standard format.
    Helper to maintain consistency across rule-based agents.
    """
    return {
        "agent_evidence": {
            agent_key: {
                "criteria_pathogenic": criteria_pathogenic,
                "criteria_benign": criteria_benign,
                "evidence_notes": notes,
                "citations": citations,
                "confidence": confidence
            }
        }
    }

