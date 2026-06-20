"""
src/utils/criteria_normalizer.py

Shared normalization utilities for ACMG criteria from LLM outputs.

LLMs often return malformed criterion names or strength values:
- Criterion names with suffixes: PS4_Supporting → PS4
- Boolean values: {PVS1: True} → {PVS1: "Very_Strong"}
- Invalid strengths: "Supporting" for PVS1 → "Strong"
- Spacing variations: "Very Strong" → "Very_Strong"

This module provides normalization functions to handle these cases.
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# Valid ACMG criterion strengths by criterion type
VALID_STRENGTHS = {
    # Very Strong criteria (can be downgraded)
    "PVS1": {"Very_Strong", "Strong", "Moderate", "Supporting"},

    # Strong criteria (can be downgraded)
    "PS1": {"Strong", "Moderate", "Supporting"},
    "PS2": {"Strong", "Moderate"},
    "PS3": {"Strong", "Moderate", "Supporting"},
    "PS4": {"Strong", "Supporting"},

    # Moderate criteria
    "PM1": {"Moderate"},
    "PM2": {"Moderate", "Supporting"},
    "PM3": {"Moderate", "Supporting"},
    "PM4": {"Moderate"},
    "PM5": {"Moderate", "Supporting"},
    "PM6": {"Moderate"},

    # Supporting criteria
    "PP1": {"Supporting"},
    "PP2": {"Supporting"},
    "PP3": {"Supporting"},
    "PP4": {"Supporting"},
    "PP5": {"Supporting"},

    # Benign Stand-Alone
    "BA1": {"Stand_Alone"},

    # Benign Strong
    "BS1": {"Strong", "Supporting"},
    "BS2": {"Strong"},
    "BS3": {"Strong", "Supporting"},
    "BS4": {"Strong", "Supporting"},

    # Benign Supporting
    "BP1": {"Supporting"},
    "BP2": {"Supporting"},
    "BP3": {"Supporting"},
    "BP4": {"Supporting"},
    "BP5": {"Supporting"},
    "BP6": {"Supporting"},
    "BP7": {"Supporting"},
}


def normalize_criterion_name(criterion: str) -> str:
    """
    Normalize criterion name by removing invalid suffixes.

    Examples:
        PS4_Supporting → PS4
        PVS1_Moderate → PVS1
        PM2_Strong → PM2

    Args:
        criterion: Raw criterion name from LLM

    Returns:
        Normalized criterion name (e.g., "PS4", "PVS1", "PM2")
    """
    # Remove common suffixes added by LLMs
    if "_" in criterion:
        base = criterion.split("_")[0]
        # Validate it's a known criterion
        if base in VALID_STRENGTHS or base.startswith(("PVS", "PS", "PM", "PP", "BA", "BS", "BP")):
            return base

    return criterion


def normalize_strength(criterion: str, strength: any) -> Optional[str]:
    """
    Normalize strength value for a given criterion.

    Handles:
    - Boolean values: True → default strength for criterion type
    - Spacing: "Very Strong" → "Very_Strong"
    - Invalid values: "Supporting" for PVS1 → "Strong"
    - Case variations: "very_strong" → "Very_Strong"

    Args:
        criterion: Normalized criterion name (e.g., "PVS1", "PS4")
        strength: Raw strength value from LLM (str, bool, or other)

    Returns:
        Normalized strength string or None if invalid
    """
    # Handle boolean values
    if isinstance(strength, bool):
        if not strength:
            return None
        # Map True to default strength for criterion type
        if criterion.startswith("PVS"):
            return "Very_Strong"
        elif criterion.startswith("PS"):
            return "Strong"
        elif criterion.startswith("PM"):
            return "Moderate"
        elif criterion.startswith("PP"):
            return "Supporting"
        elif criterion == "BA1":
            return "Stand_Alone"
        elif criterion.startswith("BS"):
            return "Strong"
        elif criterion.startswith("BP"):
            return "Supporting"
        else:
            return "Supporting"  # fallback

    # Handle non-string values
    if not isinstance(strength, str):
        return None

    # Standardize spacing and case
    strength = strength.strip().replace(" ", "_")

    # Title case each word: "very_strong" → "Very_Strong"
    parts = strength.split("_")
    strength = "_".join(word.capitalize() for word in parts)

    # Special case: StandAlone variations
    if strength in {"Stand_Alone", "StandAlone", "Standalone"}:
        strength = "Stand_Alone"

    # Validate against allowed strengths for this criterion
    if criterion in VALID_STRENGTHS:
        allowed = VALID_STRENGTHS[criterion]

        if strength not in allowed:
            # Try to map invalid strength to closest valid one
            if criterion.startswith("PVS") and strength == "Supporting":
                # PVS1 doesn't have Supporting level
                logger.warning(
                    f"Invalid strength '{strength}' for {criterion}. "
                    f"Mapping to 'Strong' (closest valid level)."
                )
                return "Strong"

            # If still invalid, use the strongest allowed
            logger.warning(
                f"Invalid strength '{strength}' for {criterion}. "
                f"Allowed: {allowed}. Using strongest: {max(allowed, key=_strength_rank)}"
            )
            return max(allowed, key=_strength_rank)

    return strength


def _strength_rank(strength: str) -> int:
    """Return numeric rank for strength comparison (higher = stronger)."""
    ranks = {
        "Stand_Alone": 100,
        "Very_Strong": 90,
        "Strong": 70,
        "Moderate": 50,
        "Supporting": 30,
    }
    return ranks.get(strength, 0)


def normalize_criteria_dict(criteria: Dict[str, any]) -> Dict[str, str]:
    """
    Normalize all criteria in a dictionary.

    Args:
        criteria: Raw criteria dict from LLM, e.g.:
            {"PS4_Supporting": True, "PM2": "Moderate", "PVS1": "supporting"}

    Returns:
        Normalized dict, e.g.:
            {"PS4": "Supporting", "PM2": "Moderate", "PVS1": "Strong"}
    """
    normalized = {}

    for raw_criterion, raw_strength in criteria.items():
        # Step 1: Normalize criterion name
        criterion = normalize_criterion_name(raw_criterion)

        # Step 2: Normalize strength
        strength = normalize_strength(criterion, raw_strength)

        # Step 3: Add to result if valid
        if strength:
            normalized[criterion] = strength
        else:
            logger.debug(
                f"Dropped invalid criterion: {raw_criterion}={raw_strength}"
            )

    return normalized

