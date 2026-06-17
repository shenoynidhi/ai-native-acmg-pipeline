
"""
src/pipeline/nodes/evidence_aggregator.py

Evidence Aggregator — applies ACMG/AMP 2015 Table 5 combination rules to
produce a preliminary_classification from all 9 agents' evidence.

ACMG Table 5 rules (Richards et al. 2015):
  Pathogenic requires ONE of:
    P1: ≥2 Very Strong (PVS1 x2 — rare)
    P2: 1 Very Strong + ≥1 Strong
    P3: 1 Very Strong + ≥2 Moderate
    P4: 1 Very Strong + ≥1 Moderate + ≥1 Supporting
    P5: 1 Very Strong + ≥2 Supporting
    P6: ≥2 Strong
    P7: 1 Strong + ≥3 Moderate
    P8: 1 Strong + ≥2 Moderate + ≥2 Supporting
    P9: 1 Strong + ≥1 Moderate + ≥4 Supporting

  Likely Pathogenic requires ONE of:
    LP1: 1 Very Strong + 1 Moderate
    LP2: 1 Strong + 1-2 Moderate
    LP3: 1 Strong + ≥2 Supporting
    LP4: ≥3 Moderate
    LP5: 2 Moderate + ≥2 Supporting
    LP6: 1 Moderate + ≥4 Supporting

  Benign requires ONE of:
    B1: 1 Stand-alone (BA1)
    B2: ≥2 Strong benign

  Likely Benign requires ONE of:
    LB1: 1 Strong benign + 1 Supporting benign
    LB2: ≥2 Supporting benign

  VUS: does not meet any of the above, or has conflicting evidence.

Strength mappings:
  Pathogenic: "Very Strong"=PVS, "Strong"=PS, "Moderate"=PM, "Supporting"=PP
  Benign:     "Stand-alone"=BA, "Strong"=BS, "Supporting"=BP

State fields read:
  agent_evidence  — dict keyed agent1..agent9

State fields written:
  all_criteria_pathogenic   — merged dict of all P criteria + strengths
  all_criteria_benign       — merged dict of all B criteria + strengths
  preliminary_classification — "Pathogenic"|"Likely_Pathogenic"|"VUS"|
                               "Likely_Benign"|"Benign"
  conflict_flag             — True if both P and B strong evidence present
  ba1_shortcircuit          — True if BA1 assigned (skip debate)
  classification_rules_met  — list of rule strings that fired e.g. ["P2","LP3"]
  aggregator_notes          — str summary of evidence counts
"""

import logging
from typing import Optional
from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strength → numeric weight for counting
# ---------------------------------------------------------------------------

# Pathogenic side
P_STRENGTH_ORDER = {
    "Very Strong": 4,
    "Strong":      3,
    "Moderate":    2,
    "Supporting":  1,
}

# Benign side
B_STRENGTH_ORDER = {
    "Stand-alone": 4,
    "Strong":      3,
    "Supporting":  1,
}

# Criterion prefix → default strength class
# Used to classify criteria that arrive with non-standard strength strings
CRITERION_CLASS = {
    # Pathogenic
    "PVS": "Very Strong",
    "PS":  "Strong",
    "PM":  "Moderate",
    "PP":  "Supporting",
    # Benign
    "BA":  "Stand-alone",
    "BS":  "Strong",
    "BP":  "Supporting",
}


def _classify_criterion(criterion: str, strength: str) -> tuple[str, str]:
    """
    Return (side, normalised_strength) for a criterion.
    side = "pathogenic" | "benign"
    Agents may override default strength (e.g. PP1 upgraded to Moderate).
    """
    criterion = criterion.strip()
    strength  = strength.strip() if strength else ""

    # Determine side from prefix
    prefix = ""
    for p in ("PVS", "PS", "PM", "PP", "BA", "BS", "BP"):
        if criterion.upper().startswith(p):
            prefix = p
            break

    if not prefix:
        logger.warning(f"Unknown criterion prefix: {criterion} — skipping")
        return "unknown", strength

    side = "benign" if prefix in ("BA", "BS", "BP") else "pathogenic"

    # Normalise strength
    default_strength = CRITERION_CLASS.get(prefix, "Supporting")
    valid_p = set(P_STRENGTH_ORDER)
    valid_b = set(B_STRENGTH_ORDER)
    valid   = valid_p if side == "pathogenic" else valid_b

    if strength in valid:
        return side, strength
    # Agent returned e.g. "Moderate" for a PP criterion — allow upgrade
    if strength in P_STRENGTH_ORDER or strength in B_STRENGTH_ORDER:
        return side, strength
    return side, default_strength


# ---------------------------------------------------------------------------
# Merge all agent evidence into flat criteria dicts
# ---------------------------------------------------------------------------

def _merge_criteria(agent_evidence: dict) -> tuple[dict, dict]:
    """
    Merge criteria_pathogenic and criteria_benign across all 9 agents.
    If two agents assign the same criterion, keep the higher strength.
    Returns (criteria_p, criteria_b).
    """
    criteria_p: dict[str, str] = {}
    criteria_b: dict[str, str] = {}

    for agent_key, evidence in agent_evidence.items():
        if not isinstance(evidence, dict):
            continue

        for criterion, strength in evidence.get("criteria_pathogenic", {}).items():
            side, norm_strength = _classify_criterion(criterion, strength)
            if side != "pathogenic":
                continue
            existing = criteria_p.get(criterion)
            if existing is None:
                criteria_p[criterion] = norm_strength
            else:
                # Keep higher strength
                if P_STRENGTH_ORDER.get(norm_strength, 0) > P_STRENGTH_ORDER.get(existing, 0):
                    criteria_p[criterion] = norm_strength
                    logger.debug(f"Upgraded {criterion}: {existing} → {norm_strength}")

        for criterion, strength in evidence.get("criteria_benign", {}).items():
            side, norm_strength = _classify_criterion(criterion, strength)
            if side != "benign":
                continue
            existing = criteria_b.get(criterion)
            if existing is None:
                criteria_b[criterion] = norm_strength
            else:
                if B_STRENGTH_ORDER.get(norm_strength, 0) > B_STRENGTH_ORDER.get(existing, 0):
                    criteria_b[criterion] = norm_strength

    return criteria_p, criteria_b


# ---------------------------------------------------------------------------
# Count criteria by strength level
# ---------------------------------------------------------------------------

def _count_pathogenic(criteria_p: dict) -> dict:
    counts = {"Very Strong": 0, "Strong": 0, "Moderate": 0, "Supporting": 0}
    for strength in criteria_p.values():
        if strength in counts:
            counts[strength] += 1
    return counts


def _count_benign(criteria_b: dict) -> dict:
    counts = {"Stand-alone": 0, "Strong": 0, "Supporting": 0}
    for strength in criteria_b.values():
        if strength in counts:
            counts[strength] += 1
    return counts


# ---------------------------------------------------------------------------
# ACMG Table 5 classification rules
# ---------------------------------------------------------------------------

def _apply_pathogenic_rules(cp: dict) -> list[str]:
    """Return list of Pathogenic rule IDs that are satisfied."""
    pvs = cp["Very Strong"]
    ps  = cp["Strong"]
    pm  = cp["Moderate"]
    pp  = cp["Supporting"]
    rules = []

    if pvs >= 2:                                    rules.append("P1")
    if pvs >= 1 and ps >= 1:                        rules.append("P2")
    if pvs >= 1 and pm >= 2:                        rules.append("P3")
    if pvs >= 1 and pm >= 1 and pp >= 1:            rules.append("P4")
    if pvs >= 1 and pp >= 2:                        rules.append("P5")
    if ps  >= 2:                                    rules.append("P6")
    if ps  >= 1 and pm >= 3:                        rules.append("P7")
    if ps  >= 1 and pm >= 2 and pp >= 2:            rules.append("P8")
    if ps  >= 1 and pm >= 1 and pp >= 4:            rules.append("P9")
    return rules


def _apply_likely_pathogenic_rules(cp: dict) -> list[str]:
    pvs = cp["Very Strong"]
    ps  = cp["Strong"]
    pm  = cp["Moderate"]
    pp  = cp["Supporting"]
    rules = []

    if pvs >= 1 and pm == 1:                        rules.append("LP1")
    if ps  >= 1 and 1 <= pm <= 2:                   rules.append("LP2")
    if ps  >= 1 and pp >= 2:                        rules.append("LP3")
    if pm  >= 3:                                    rules.append("LP4")
    if pm  >= 2 and pp >= 2:                        rules.append("LP5")
    if pm  >= 1 and pp >= 4:                        rules.append("LP6")
    return rules


def _apply_benign_rules(cb: dict) -> list[str]:
    ba = cb["Stand-alone"]
    bs = cb["Strong"]
    bp = cb["Supporting"]
    rules = []

    if ba >= 1:                                     rules.append("B1")
    if bs >= 2:                                     rules.append("B2")
    return rules


def _apply_likely_benign_rules(cb: dict) -> list[str]:
    bs = cb["Strong"]
    bp = cb["Supporting"]
    rules = []

    if bs >= 1 and bp >= 1:                         rules.append("LB1")
    if bp >= 2:                                     rules.append("LB2")
    return rules


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def _has_conflict(
    p_rules: list, lp_rules: list,
    b_rules: list, lb_rules: list,
    cp: dict, cb: dict,
) -> bool:
    """
    Conflict = meaningful pathogenic AND meaningful benign evidence both present.
    Threshold: ≥1 Strong/Very Strong on each side.
    """
    p_strong  = cp["Very Strong"] + cp["Strong"] > 0
    b_strong  = cb["Stand-alone"] + cb["Strong"] > 0
    return bool((p_rules or lp_rules) and (b_rules or lb_rules)) or (p_strong and b_strong)


# ---------------------------------------------------------------------------
# Final classification decision
# ---------------------------------------------------------------------------

def _classify(
    p_rules:  list,
    lp_rules: list,
    b_rules:  list,
    lb_rules: list,
    ba1_fired: bool,
    conflict:  bool,
) -> str:
    # BA1 is stand-alone Benign regardless of other evidence
    if ba1_fired:
        return "Benign"

    # Conflict → VUS (debate nodes will resolve)
    if conflict:
        return "VUS"

    # Pathogenic trumps Likely Pathogenic
    if p_rules:
        return "Pathogenic"
    if lp_rules:
        return "Likely_Pathogenic"
    if b_rules:
        return "Benign"
    if lb_rules:
        return "Likely_Benign"
    return "VUS"


# ---------------------------------------------------------------------------
# Main node function
# ---------------------------------------------------------------------------

def evidence_aggregator_node(state: VariantState) -> dict:
    """
    Merge all agent evidence and apply ACMG Table 5 combination rules.
    Sets preliminary_classification, conflict_flag, ba1_shortcircuit.
    """
    variant_id     = state.get("variant_id", "?")
    agent_evidence = state.get("agent_evidence", {})

    logger.info(f" Aggregating evidence for {variant_id}")

    if not agent_evidence:
        logger.warning(f" No agent evidence found for {variant_id}")
        # Check if clinical notes provided to determine unevaluated criteria
        clinical_notes = state.get("clinical_notes") or state.get("clinical_history") or ""
        unevaluated = ["PP4", "BP5"] if not clinical_notes.strip() else []

        return {
            "all_criteria_pathogenic":   {},
            "all_criteria_benign":       {},
            "preliminary_classification": "VUS",
            "conflict_flag":             False,
            "ba1_shortcircuit":          False,
            "classification_rules_met":  [],
            "aggregator_notes":          "No agent evidence available.",
            "pathogenic_counts":         {"Very Strong":0,"Strong":0,"Moderate":0,"Supporting":0},
            "benign_counts":             {"Stand-alone":0,"Strong":0,"Supporting":0},
            "unevaluated_criteria":      unevaluated,

        }

    # Merge
    criteria_p, criteria_b = _merge_criteria(agent_evidence)

    # BA1 short-circuit check (stand-alone benign — AF > 5%)
    ba1_fired = "BA1" in criteria_b

    # Count by strength
    cp = _count_pathogenic(criteria_p)
    cb = _count_benign(criteria_b)

    # Detect whether agent9 ran and if clinical notes provided (for PP4/BP5 flag)
    agent9_ran = bool(agent_evidence.get("agent9"))
    clinical_notes = state.get("clinical_notes") or state.get("clinical_history") or ""
    unevaluated: list[str] = []
    if not agent9_ran and not clinical_notes.strip():
        unevaluated.extend(["PP4", "BP5"])

    # Apply rules
    p_rules  = _apply_pathogenic_rules(cp)
    lp_rules = _apply_likely_pathogenic_rules(cp)
    b_rules  = _apply_benign_rules(cb)
    lb_rules = _apply_likely_benign_rules(cb)

    all_rules = p_rules + lp_rules + b_rules + lb_rules

    # Conflict detection
    conflict = _has_conflict(p_rules, lp_rules, b_rules, lb_rules, cp, cb)

    # Classification
    classification = _classify(p_rules, lp_rules, b_rules, lb_rules, ba1_fired, conflict)

    # Summary notes
    aggregator_notes = (
        f"Pathogenic evidence — PVS:{cp['Very Strong']} PS:{cp['Strong']} "
        f"PM:{cp['Moderate']} PP:{cp['Supporting']}. "
        f"Benign evidence — BA:{cb['Stand-alone']} BS:{cb['Strong']} "
        f"BP:{cb['Supporting']}. "
        f"Rules met: {all_rules or 'none'}. "
        f"Conflict: {conflict}. "
        f"Preliminary: {classification}."
    )

    logger.info(
        f"[evidence_aggregator] {variant_id}: {classification} "
        f"rules={all_rules} conflict={conflict} BA1={ba1_fired}"
    )
# Merge citations from all agents
    all_citations: list[str] = []
    for agent_key, evidence in agent_evidence.items():
        if isinstance(evidence, dict):
            all_citations.extend(evidence.get("citations", []))
    all_citations = list(dict.fromkeys(all_citations))  # deduplicate, preserve order
    return {
        "all_criteria_pathogenic":    criteria_p,
        "all_criteria_benign":        criteria_b,
        "preliminary_classification": classification,
        "conflict_flag":              conflict,
        "ba1_shortcircuit":           ba1_fired,
        "classification_rules_met":   all_rules,
        "unevaluated_criteria": unevaluated,
        "pathogenic_counts": cp,   # {"Very Strong":1, "Strong":0, ...}
        "benign_counts":     cb,
        "aggregator_notes": aggregator_notes,
        "all_citations": all_citations,
    }

