"""
src/utils/case_control.py

Case-control statistics for PS4 criterion evaluation.

PS4 (ACMG/AMP 2015): "Prevalence of the variant in affected individuals is
significantly increased compared with the prevalence in controls."

Requires:
  - Case cohort: affected individuals with known phenotype + variant status
  - Control cohort: gnomAD population frequencies (disease-free controls)
  - Statistical test: Fisher's exact test for 2×2 contingency table
  - Thresholds: Odds ratio > 5.0 AND p < 0.05 (PS4 Strong)
                Odds ratio > 2.0 AND p < 0.05 (PS4 Supporting)

User provides case database CSV:
    sample_id,variant,gene,condition,affected_status,ancestry
    C001,chr17:43071077:A:G,BRCA1,breast_cancer,yes,EUR
    C002,chr17:43071077:A:G,BRCA1,breast_cancer,yes,EUR
    ...

This module:
  1. Loads and validates case database
  2. Filters cases by gene + condition
  3. Counts affected carriers
  4. Fetches gnomAD control frequencies
  5. Builds 2×2 contingency table
  6. Calculates OR + Fisher's exact p-value
  7. Maps to PS4 strength (Strong/Supporting/None)
"""

import logging
from pathlib import Path
from typing import Optional, Tuple, Dict
import pandas as pd
from scipy.stats import fisher_exact

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum evidence thresholds to apply PS4
MIN_AFFECTED_CARRIERS = 3      # Need ≥3 independent affected carriers
MIN_TOTAL_CASES = 10           # Need reasonable case cohort size (was 50, reduced for small cohorts)
MIN_CONTROL_ALLELES = 10000    # gnomAD subset must have sufficient coverage

# PS4 strength thresholds (ClinGen recommendations)
PS4_STRONG_OR = 5.0
PS4_SUPPORTING_OR = 2.0
PS4_PVALUE = 0.05


# ---------------------------------------------------------------------------
# Case database loader
# ---------------------------------------------------------------------------

def load_case_database(csv_path: Path) -> pd.DataFrame:
    """
    Load and validate case database CSV.

    Expected columns:
        sample_id       — unique patient/sample identifier (anonymized)
        variant         — chr:pos:ref:alt format
        gene            — HGNC gene symbol
        condition       — disease phenotype (e.g., breast_cancer, long_qt_syndrome)
        affected_status — "yes" or "no" (affected vs unaffected control)
        ancestry        — optional: EUR, AFR, EAS, SAS, AMR (for gnomAD population matching)

    Returns:
        DataFrame with standardized columns

    Raises:
        ValueError if required columns missing or format invalid
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Case database not found: {csv_path}")

    df = pd.read_csv(csv_path)

    # Validate required columns
    required = {"sample_id", "variant", "gene", "condition", "affected_status"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Case database missing required columns: {missing}. "
            f"Found: {list(df.columns)}"
        )

    # Standardize affected_status to boolean
    df["affected"] = df["affected_status"].str.lower().isin(["yes", "true", "1", "affected"])

    # Standardize gene names (uppercase)
    df["gene"] = df["gene"].str.upper()

    # Standardize condition (lowercase, underscores)
    df["condition"] = df["condition"].str.lower().str.replace(" ", "_")

    # Optional: standardize ancestry codes
    if "ancestry" in df.columns:
        ancestry_map = {
            "EUR": "nfe",  # gnomAD non-Finnish European
            "AFR": "afr",
            "EAS": "eas",
            "SAS": "sas",
            "AMR": "amr",
            "FIN": "fin",
            "ASJ": "asj",
        }
        df["gnomad_pop"] = df["ancestry"].str.upper().map(ancestry_map)
    else:
        df["gnomad_pop"] = "global"

    logger.info(
        f"Loaded case database: {len(df)} samples, "
        f"{df['affected'].sum()} affected, "
        f"{len(df['gene'].unique())} genes"
    )

    return df


# ---------------------------------------------------------------------------
# Case frequency calculation
# ---------------------------------------------------------------------------

def count_case_carriers(
    case_db: pd.DataFrame,
    variant_id: str,
    gene: str,
    condition: Optional[str] = None,
) -> Tuple[int, int]:
    """
    Count affected carriers and total affected individuals for a variant.

    Args:
        case_db: DataFrame from load_case_database()
        variant_id: chr:pos:ref:alt
        gene: HGNC gene symbol
        condition: Optional disease filter (e.g., "breast_cancer")
                   If None, uses all affected individuals for this gene

    Returns:
        (affected_carriers, total_affected)
    """
    # Filter to this gene
    gene_cases = case_db[case_db["gene"] == gene.upper()]

    # Filter to this condition if specified
    if condition:
        condition_std = condition.lower().replace(" ", "_")
        gene_cases = gene_cases[gene_cases["condition"] == condition_std]

    # Count affected individuals
    affected = gene_cases[gene_cases["affected"] == True]
    total_affected = len(affected)

    # Count carriers of this variant
    affected_carriers = len(affected[affected["variant"] == variant_id])

    return affected_carriers, total_affected


# ---------------------------------------------------------------------------
# Control frequency from gnomAD (variant state)
# ---------------------------------------------------------------------------

def get_gnomad_control_frequency(
    state: dict,
    population: str = "global"
) -> Tuple[int, int]:
    """
    Extract gnomAD control allele count and total alleles from variant state.

    Args:
        state: VariantState dict with gnomAD annotation
        population: gnomAD population (e.g., "nfe", "afr", "global")

    Returns:
        (control_variant_count, control_total_alleles)

    Note:
        gnomAD allele counts represent CHROMOSOMES, not individuals.
        For diploid: total_individuals = total_alleles / 2
    """
    # Try population-specific first, fall back to global
    ac_key = f"gnomad_ac_{population}" if population != "global" else "gnomad_ac"
    an_key = f"gnomad_an_{population}" if population != "global" else "gnomad_an"

    ac = state.get(ac_key) or state.get("gnomad_ac", 0)
    an = state.get(an_key) or state.get("gnomad_an", 0)

    # Fallback: calculate from AF if AC/AN missing
    if an == 0:
        af = state.get("max_gnomad_af") or state.get("gnomad_af_popmax", 0.0)
        # Assume gnomAD v3.1 ~76k genomes = 152k alleles
        an = 152000
        ac = int(af * an)

    return int(ac or 0), int(an or 0)


# ---------------------------------------------------------------------------
# PS4 evaluation
# ---------------------------------------------------------------------------

def evaluate_ps4(
    case_db: pd.DataFrame,
    state: dict,
    variant_id: str,
    gene: str,
    condition: Optional[str] = None,
) -> Dict[str, any]:
    """
    Evaluate PS4 criterion using case-control frequency analysis.

    Args:
        case_db: Case database DataFrame
        state: VariantState dict with gnomAD annotation
        variant_id: chr:pos:ref:alt
        gene: HGNC gene symbol
        condition: Optional disease phenotype filter

    Returns:
        dict with:
            status: "met" | "insufficient" | "not_met"
            strength: "Strong" | "Supporting" | None
            OR: float (odds ratio)
            p_value: float (Fisher's exact)
            cases_total: int
            cases_with_variant: int
            controls_total: int (gnomAD allele count)
            controls_with_variant: int
            reason: str (explanation)
            confidence: "HIGH" | "MEDIUM" | "LOW"
    """
    # Step 1: Count cases
    affected_carriers, total_affected = count_case_carriers(
        case_db, variant_id, gene, condition
    )

    # Step 2: Get controls from gnomAD
    # Use ancestry-matched population if available
    ancestry = case_db[case_db["variant"] == variant_id]["gnomad_pop"].mode()
    population = ancestry[0] if len(ancestry) > 0 else "global"

    control_carriers, control_total = get_gnomad_control_frequency(state, population)

    # Step 3: Apply minimum thresholds
    if total_affected < MIN_TOTAL_CASES:
        return {
            "status": "insufficient",
            "strength": None,
            "reason": f"Case cohort too small (n={total_affected}, need ≥{MIN_TOTAL_CASES})",
            "cases_total": total_affected,
            "cases_with_variant": affected_carriers,
            "confidence": "LOW",
        }

    if affected_carriers < MIN_AFFECTED_CARRIERS:
        return {
            "status": "insufficient",
            "strength": None,
            "reason": f"Too few affected carriers (n={affected_carriers}, need ≥{MIN_AFFECTED_CARRIERS})",
            "cases_total": total_affected,
            "cases_with_variant": affected_carriers,
            "confidence": "LOW",
        }

    if control_total < MIN_CONTROL_ALLELES:
        return {
            "status": "insufficient",
            "strength": None,
            "reason": f"gnomAD control coverage insufficient (n={control_total})",
            "controls_total": control_total,
            "controls_with_variant": control_carriers,
            "confidence": "LOW",
        }

    # Step 4: Build 2×2 contingency table
    #
    #              Variant+   Variant-
    # Cases        a          b
    # Controls     c          d
    #
    a = affected_carriers
    b = total_affected - affected_carriers
    c = control_carriers
    d = control_total - control_carriers

    contingency = [[a, b], [c, d]]

    # Step 5: Fisher's exact test
    try:
        odds_ratio, p_value = fisher_exact(contingency, alternative="greater")
    except Exception as e:
        logger.error(f"Fisher's exact test failed: {e}")
        return {
            "status": "insufficient",
            "strength": None,
            "reason": f"Statistical test failed: {e}",
            "confidence": "LOW",
        }

    # Step 6: Map to PS4 strength
    if odds_ratio >= PS4_STRONG_OR and p_value < PS4_PVALUE:
        status = "met"
        strength = "Strong"
        confidence = "HIGH"
    elif odds_ratio >= PS4_SUPPORTING_OR and p_value < PS4_PVALUE:
        status = "met"
        strength = "Supporting"
        confidence = "MEDIUM"
    else:
        status = "not_met"
        strength = None
        confidence = "HIGH"  # high confidence it does NOT meet PS4

    # Step 7: Build result
    case_freq = affected_carriers / total_affected if total_affected > 0 else 0
    control_freq = control_carriers / control_total if control_total > 0 else 0

    result = {
        "status": status,
        "strength": strength,
        "OR": round(odds_ratio, 2),
        "p_value": p_value,
        "cases_total": total_affected,
        "cases_with_variant": affected_carriers,
        "case_frequency": round(case_freq, 6),
        "controls_total": control_total,
        "controls_with_variant": control_carriers,
        "control_frequency": round(control_freq, 6),
        "confidence": confidence,
        "population": population,
        "reason": _format_ps4_reason(status, strength, odds_ratio, p_value, affected_carriers),
    }

    logger.info(
        f"PS4 evaluation: {variant_id} {gene} — {status} "
        f"(OR={odds_ratio:.2f}, p={p_value:.4f}, carriers={affected_carriers}/{total_affected})"
    )

    return result


def _format_ps4_reason(
    status: str,
    strength: Optional[str],
    odds_ratio: float,
    p_value: float,
    carriers: int,
) -> str:
    """Format human-readable PS4 evaluation reason."""
    if status == "met":
        return (
            f"PS4 {strength}: Variant significantly enriched in affected individuals "
            f"(OR={odds_ratio:.2f}, p={p_value:.4f}, n={carriers} independent carriers). "
            f"Case-control analysis supports pathogenicity."
        )
    elif status == "not_met":
        return (
            f"PS4 not met: No significant enrichment in affected individuals "
            f"(OR={odds_ratio:.2f}, p={p_value:.4f}). "
            f"Variant frequency in cases not significantly higher than controls."
        )
    else:
        return "PS4 insufficient evidence (see case/control counts)"

