"""
src/agents/agent2_consequence.py
Agent 2 — Consequence / Loss-of-Function Criteria (PVS1)

Deterministic evidence engine implementing the ClinGen SVI PVS1 recommendations
(Abou Tayoun et al. 2018, PMC6185798). start_lost is a fully separate branch,
never gated by LOFTEE (which does not evaluate start_lost at all). Canonical
and non-canonical splice branches use SpliceAI delta scores and LOFTEE's own
LoF_info fields (MaxEntScan / de-novo-rescue-probability) as independent
corroborating/discordance-checking evidence.

DESIGN PRINCIPLES
------------------
1. 100% rule-based. No LLM call — PVS1 is well-defined published logic, and
   this pipeline already has enough non-determinism sources elsewhere
   (see debate layer). Adding one here would undermine reproducibility on
   the single highest-weight ACMG criterion.
2. Three-state evidence everywhere: True / False / None ("unknown"). Unknown
   evidence is NEVER silently coerced to False. A branch lacking data for a
   confident call returns a conservative strength AND flags
   review_required=True, rather than guessing.
3. LOFTEE (`is_loftee_hc`) only gates consequence types LOFTEE actually
   evaluates: stop_gained, frameshift_variant, splice_acceptor_variant,
   splice_donor_variant. NEVER used to downgrade start_lost.
4. Gene-level constraint metrics (pLI, LOEUF) are supporting context only —
   not sufficient alone to establish "LoF is the disease mechanism."
5. Splice calls are cross-checked against SpliceAI delta scores and LOFTEE's
   LoF_info rescue-probability/MES fields where available, rather than
   trusting a single predictor's HC/LC call in isolation.

State fields read (existing):
  consequence, is_loftee_hc, gene, gene_clingen_validity,
  gene_orphanet_inheritance, gene_gnomad_pli, gene_gnomad_loeuf,
  exon_number, intron_number, hgvsc, hgvsp, transcript,
  clinvar_classification, clinvar_review_stars, gene_clinvar_lof_fraction

State fields read (new — optional, degrades gracefully if absent):
  lof_filter, lof_flags, lof_info          (raw LOFTEE strings from VEP TSV)
  spliceai_ds_ag / ds_al / ds_dg / ds_dl    (confirm actual field names —
                                              see _extract_evidence below)
  gene_clinvar_lof_count, gene_clinvar_lof_multi_exon  (from Agent 4 — see
                                              module docstring / conversation
                                              notes for what these represent)
  protein_position, cds_position           ("pos/total" if --total_length
                                              added to VEP command; optional)

State fields written (via agent_evidence):
  agent_evidence["agent2"]
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from src.pipeline.state import VariantState
from src.utils.logging_config import get_user_friendly_logger

logger = get_user_friendly_logger("agent2_consequence")

# ---------------------------------------------------------------------------
# Consequence classes
# ---------------------------------------------------------------------------
NMD_TRUNCATING_CONSEQUENCES = {"stop_gained", "frameshift_variant"}
CANONICAL_SPLICE_CONSEQUENCES = {"splice_acceptor_variant", "splice_donor_variant"}
INITIATION_CODON_CONSEQUENCES = {"start_lost"}
SPLICE_REGION_CONSEQUENCES = {
    "splice_region_variant",
    "splice_donor_5th_base_variant",
    "splice_donor_region_variant",
    "splice_polypyrimidine_tract_variant",
}
LOFTEE_EVALUATED_CONSEQUENCES = NMD_TRUNCATING_CONSEQUENCES | CANONICAL_SPLICE_CONSEQUENCES

ALL_PVS1_ELIGIBLE_CONSEQUENCES = (
    NMD_TRUNCATING_CONSEQUENCES
    | CANONICAL_SPLICE_CONSEQUENCES
    | INITIATION_CODON_CONSEQUENCES
    | SPLICE_REGION_CONSEQUENCES
)

CNV_CONSEQUENCES = {"transcript_ablation", "transcript_amplification"}

# Constraint thresholds — supporting evidence only, never sufficient alone
PLI_CONSTRAINED = 0.9
LOEUF_CONSTRAINED = 0.35
LOF_FRACTION_STRONG = 0.30

# SpliceAI standard cutoffs (Jaganathan et al. 2019)
SPLICEAI_HIGH = 0.8
SPLICEAI_MEDIUM = 0.5
SPLICEAI_LOW = 0.2

STRENGTH_ORDER = ["Very_Strong", "Strong", "Moderate", "Supporting"]

PVS1_CITATIONS = [
    "ACMG/AMP 2015",
    "ClinGen SVI PVS1 recommendations (Abou Tayoun et al. 2018, PMC6185798)",
]


# ---------------------------------------------------------------------------
# Evidence model
# ---------------------------------------------------------------------------
@dataclass
class PVS1Evidence:
    variant_id: str
    gene: str
    consequence: str
    transcript: Optional[str] = None

    clingen_validity: Optional[str] = None
    pli: Optional[float] = None
    loeuf: Optional[float] = None
    clinvar_lof_fraction: Optional[float] = None
    clinvar_lof_count: Optional[int] = None
    clinvar_lof_multi_exon: Optional[bool] = None

    exon_number: Optional[str] = None
    intron_number: Optional[str] = None
    hgvsc: Optional[str] = None
    hgvsp: Optional[str] = None

    is_loftee_hc: Optional[bool] = None
    lof_filter: Optional[str] = None
    lof_flags: Optional[str] = None
    lof_info: Optional[str] = None

    # SpliceAI: post_process.py's _parse_spliceai() collapses the four
    # DS_AG/DS_AL/DS_DG/DS_DL scores to a single max delta before this ever
    # reaches VariantState (field name: max_spliceai). Donor/acceptor- or
    # gain/loss-specific reasoning is NOT possible with current pipeline data;
    # only "SpliceAI predicts disruption somewhere nearby, magnitude X" is.
    max_spliceai: Optional[float] = None

    protein_position_raw: Optional[str] = None
    cds_position_raw: Optional[str] = None


@dataclass
class PVS1Result:
    strength: Optional[str]
    status: str
    reasons: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    review_required: bool = False


# ---------------------------------------------------------------------------
# Evidence extraction from VariantState
# ---------------------------------------------------------------------------
def _first_present(state: dict, keys: list, default=None):
    for k in keys:
        v = state.get(k)
        if v is not None:
            return v
    return default


def _as_float(v) -> Optional[float]:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_evidence(state: VariantState) -> PVS1Evidence:
    return PVS1Evidence(
        variant_id=state.get("variant_id", "?"),
        gene=state.get("gene", "UNKNOWN"),
        consequence=state.get("consequence", "") or "",
        transcript=state.get("transcript"),
        clingen_validity=state.get("gene_clingen_validity"),
        pli=state.get("gene_gnomad_pli"),
        loeuf=state.get("gene_gnomad_loeuf"),
        clinvar_lof_fraction=state.get("gene_clinvar_lof_fraction"),
        clinvar_lof_count=state.get("gene_clinvar_lof_count"),
        clinvar_lof_multi_exon=state.get("gene_clinvar_lof_multi_exon"),
        exon_number=state.get("exon_number"),
        intron_number=state.get("intron_number"),
        hgvsc=state.get("hgvsc"),
        hgvsp=state.get("hgvsp"),
        is_loftee_hc=state.get("is_loftee_hc"),
        lof_filter=state.get("lof_filter"),
        lof_flags=state.get("lof_flags"),
        lof_info=state.get("lof_info"),
        # Confirmed field name from post_process.py: max_spliceai (single float,
        # already max()'d across DS_AG/DS_AL/DS_DG/DS_DL in _parse_spliceai()).
        max_spliceai=_as_float(state.get("max_spliceai")),
        protein_position_raw=state.get("protein_position"),
        cds_position_raw=state.get("cds_position"),
    )


# ---------------------------------------------------------------------------
# Gene-level LoF mechanism (tiered)
# ---------------------------------------------------------------------------
def _evaluate_lof_mechanism(e: PVS1Evidence) -> tuple[Optional[bool], int, list[str]]:
    """
    Returns (established, downgrade_levels, reasons).
    established=True + downgrade 0/1/2 tiers the strength cap;
    established=False -> not applicable; established=None -> insufficient
    evidence, treated conservatively (not established).
    """
    validity = (e.clingen_validity or "").lower()
    reasons = []

    if (
        validity in {"strong", "definitive"}
        and e.clinvar_lof_count is not None
        and e.clinvar_lof_count >= 3
        and e.clinvar_lof_fraction is not None
        and e.clinvar_lof_fraction > 0.10
        and e.clinvar_lof_multi_exon is True
    ):
        reasons.append(
            f"ClinGen validity={e.clingen_validity}; {e.clinvar_lof_count} pathogenic LoF "
            f"variants ({e.clinvar_lof_fraction:.0%} of P/LP), spanning >1 exon"
        )
        return True, 0, reasons

    if validity in {"strong", "definitive"}:
        reasons.append(
            f"ClinGen validity={e.clingen_validity} (LoF track-record counts unavailable "
            f"from Agent 4 — capped one tier below full strength)"
        )
        return True, 1, reasons

    if validity == "moderate" and (
        (e.clinvar_lof_count is not None and e.clinvar_lof_count >= 2)
        or (e.clinvar_lof_fraction is not None and e.clinvar_lof_fraction >= LOF_FRACTION_STRONG)
    ):
        reasons.append("ClinGen validity=Moderate with supporting LoF variant evidence")
        return True, 1, reasons

    supporting_signals = []
    if e.pli is not None and e.pli >= PLI_CONSTRAINED:
        supporting_signals.append(f"pLI={e.pli:.3f}")
    if e.loeuf is not None and e.loeuf <= LOEUF_CONSTRAINED:
        supporting_signals.append(f"LOEUF={e.loeuf:.3f}")
    if e.clinvar_lof_fraction is not None and e.clinvar_lof_fraction >= LOF_FRACTION_STRONG:
        supporting_signals.append(f"ClinVar LoF fraction={e.clinvar_lof_fraction:.0%}")

    if validity in {"moderate", "limited"} and supporting_signals:
        reasons.append(f"ClinGen validity={e.clingen_validity}; supporting: " + ", ".join(supporting_signals))
        return True, 2, reasons

    if not validity and supporting_signals:
        reasons.append(
            "No ClinGen validity on record; constraint/ClinVar signals present but NOT "
            "sufficient alone to establish LoF as disease mechanism: " + ", ".join(supporting_signals)
        )
        return None, 2, reasons

    if validity in {"no known disease relationship", "disputed", "refuted"}:
        reasons.append(f"ClinGen validity={e.clingen_validity} — LoF mechanism not supported")
        return False, 0, reasons

    reasons.append("Insufficient evidence that LoF is an established disease mechanism for this gene")
    return None, 2, reasons


def _downgrade_strength(strength: Optional[str], levels: int) -> Optional[str]:
    if strength not in STRENGTH_ORDER or levels <= 0:
        return strength
    i = STRENGTH_ORDER.index(strength)
    return STRENGTH_ORDER[min(i + levels, len(STRENGTH_ORDER) - 1)]


# ---------------------------------------------------------------------------
# Exon / NMD helpers
# ---------------------------------------------------------------------------
def _parse_fraction(pos_str: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not pos_str:
        return None, None
    parts = pos_str.split("/")
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


def _is_last_exon(exon_number: Optional[str]) -> Optional[bool]:
    n, total = _parse_fraction(exon_number)
    return None if n is None else n == total


def _is_penultimate_exon(exon_number: Optional[str]) -> Optional[bool]:
    n, total = _parse_fraction(exon_number)
    if n is None or total is None:
        return None
    return n == total - 1


def _parse_lof_info(lof_info: Optional[str]) -> dict:
    """
    Parses LOFTEE's LoF_info 'KEY:VALUE,KEY:VALUE,...' string into a dict.
    Values are cast to float where possible, else kept as string.
    """
    out = {}
    if not lof_info:
        return out
    for token in lof_info.split(","):
        if ":" not in token:
            continue
        k, _, v = token.partition(":")
        k = k.strip()
        v = v.strip()
        fv = _as_float(v)
        out[k] = fv if fv is not None else v
    return out


def _parse_lof_flags(e: PVS1Evidence) -> dict:
    filt = e.lof_filter or ""
    flags = e.lof_flags or ""
    combined = f"{filt};{flags}"
    return {
        "splice_rescue_flag": bool(re.search(r"RESCUE_(DONOR|ACCEPTOR)", combined)),
        "single_exon": "SINGLE_EXON" in combined,
        "end_trunc": "END_TRUNC" in combined,
        "incomplete_cds": "INCOMPLETE_CDS" in combined,
        "non_canonical_splice": bool(re.search(r"NON_(DONOR|ACCEPTOR)_DISRUPTING|NON_CAN_SPLICE", combined)),
    }


def _evaluate_nmd(e: PVS1Evidence) -> tuple[Optional[bool], str]:
    flags = _parse_lof_flags(e)
    if flags["single_exon"]:
        return False, "LOFTEE flags SINGLE_EXON — no downstream exon-exon junction, NMD does not apply"

    last_exon = _is_last_exon(e.exon_number)
    if last_exon is True:
        return False, f"PTC in final exon ({e.exon_number}) — NMD not predicted"

    penultimate = _is_penultimate_exon(e.exon_number)
    if penultimate is True:
        return None, (
            f"PTC in penultimate exon ({e.exon_number}) — cannot determine without transcript "
            f"coordinates whether it falls within the last 50nt (NMD-escape zone); review required"
        )

    if last_exon is False and penultimate is False:
        return True, f"PTC in exon {e.exon_number}, well upstream of final exon-exon junction — NMD predicted"

    return None, "Insufficient exon position data to predict NMD"


def _protein_fraction_lost(e: PVS1Evidence) -> tuple[Optional[float], str]:
    pos, total = _parse_fraction(e.protein_position_raw)
    if pos is not None and total:
        frac = 1 - (pos / total)
        return frac, f"Exact: {pos}/{total} aa position -> ~{frac:.0%} of protein lost"

    n, total_exons = _parse_fraction(e.exon_number)
    if n is not None and total_exons:
        frac = 1 - (n / total_exons)
        return frac, (
            f"Approximate (exon-position proxy, not exact protein coordinates): "
            f"exon {n}/{total_exons} -> ~{frac:.0%} proxy for protein fraction lost. "
            f"Add --total_length to VEP for exact protein-position-based calculation."
        )
    return None, "No exon or protein position data available to estimate protein loss"


# ---------------------------------------------------------------------------
# Splice-evidence cross-check (SpliceAI + LOFTEE LoF_info)
# ---------------------------------------------------------------------------
def _rescue_signal(e: PVS1Evidence) -> tuple[bool, list[str]]:
    """
    Combines LOFTEE's de-novo-rescue probability (LoF_info — requires
    post_process.py to be extended to pass this field through; degrades
    cleanly to no signal if absent) with LOFTEE's own rescue flags.
    NOTE: max_spliceai is a single collapsed score (no gain/loss distinction
    available from current pipeline data), so it is NOT used here to infer
    rescue specifically — see _evaluate_canonical_splice for how it IS used
    (as general corroboration of disruption, not rescue detection).
    """
    info = _parse_lof_info(e.lof_info)
    notes = []
    rescue = False

    for prob_key in ("DE_NOVO_DONOR_PROB", "DE_NOVO_ACCEPTOR_PROB"):
        p = info.get(prob_key)
        if isinstance(p, float) and p >= 0.2:
            notes.append(f"LOFTEE {prob_key}={p:.3f} (de novo rescue site plausible)")
            rescue = True

    flags = _parse_lof_flags(e)
    if flags["splice_rescue_flag"]:
        notes.append("LOFTEE LoF_filter/flags indicate a nearby annotated rescue splice site")
        rescue = True

    if e.lof_info is None:
        notes.append(
            "lof_info not present in VariantState — post_process.py currently does not "
            "pass LoF_info through; de-novo-rescue-probability check skipped, not assumed false"
        )

    return rescue, notes


# ---------------------------------------------------------------------------
# Branch: nonsense / frameshift (NMD-truncating pathway)
# ---------------------------------------------------------------------------
def _evaluate_nonsense_frameshift(e: PVS1Evidence, mech_established: Optional[bool], downgrade: int) -> PVS1Result:
    reasons = [f"Consequence: {e.consequence}"]
    caveats = []

    if mech_established is False:
        return PVS1Result(None, "NOT_APPLICABLE", reasons + ["LoF not established as disease mechanism"], [], False)
    if mech_established is None:
        caveats.append("LoF mechanism evidence insufficient — capped at Supporting")
        return PVS1Result("Supporting", "REVIEW_REQUIRED", reasons, caveats, True)

    if e.is_loftee_hc is False:
        caveats.append("LOFTEE: low-confidence LoF call")
        return PVS1Result("Moderate", "APPLIED", reasons, caveats, False)
    if e.is_loftee_hc is None:
        caveats.append("LOFTEE confidence not available for this variant — review recommended")
        return PVS1Result("Moderate", "REVIEW_REQUIRED", reasons, caveats, True)

    flags = _parse_lof_flags(e)
    if flags["non_canonical_splice"] or flags["incomplete_cds"]:
        caveats.append("LOFTEE flags indicate uncertain transcript/splice context")
        return PVS1Result("Moderate", "REVIEW_REQUIRED", reasons, caveats, True)

    nmd_predicted, nmd_reason = _evaluate_nmd(e)
    reasons.append(nmd_reason)

    if nmd_predicted is True:
        return PVS1Result(_downgrade_strength("Very_Strong", downgrade), "APPLIED", reasons, caveats, False)

    if nmd_predicted is False:
        frac, frac_reason = _protein_fraction_lost(e)
        reasons.append(frac_reason)
        if frac is not None and frac > 0.10:
            base = "Strong"
        elif frac is not None:
            base = "Moderate"
        else:
            caveats.append("Cannot estimate protein fraction lost — capped at Moderate")
            base = "Moderate"
        return PVS1Result(_downgrade_strength(base, downgrade), "APPLIED", reasons, caveats, frac is None)

    caveats.append("NMD status undetermined (see above) — capped at Moderate pending review")
    return PVS1Result("Moderate", "REVIEW_REQUIRED", reasons, caveats, True)


# ---------------------------------------------------------------------------
# Branch: canonical splice (±1/±2), now cross-checked against SpliceAI + LoF_info
# ---------------------------------------------------------------------------
def _evaluate_canonical_splice(e: PVS1Evidence, mech_established: Optional[bool], downgrade: int) -> PVS1Result:
    reasons = [f"Consequence: {e.consequence}"]
    caveats = []

    if mech_established is False:
        return PVS1Result(None, "NOT_APPLICABLE", reasons + ["LoF not established as disease mechanism"], [], False)
    if mech_established is None:
        caveats.append("LoF mechanism evidence insufficient — capped at Supporting")
        return PVS1Result("Supporting", "REVIEW_REQUIRED", reasons, caveats, True)

    # max_spliceai is a single collapsed score (post_process.py's _parse_spliceai
    # takes max across DS_AG/DS_AL/DS_DG/DS_DL) — cannot distinguish "this variant
    # disrupts the native site" from "this variant creates a site elsewhere."
    # Still useful as a coarse discordance check: if VEP/LOFTEE call canonical
    # splice but SpliceAI sees essentially nothing predicted nearby at all,
    # that combination is worth a second look.
    max_score = e.max_spliceai
    if max_score is not None and max_score < SPLICEAI_LOW:
        caveats.append(
            f"SpliceAI max delta score={max_score:.2f} (<{SPLICEAI_LOW}) despite canonical splice "
            f"consequence call — possible annotation discordance, review recommended. Note: "
            f"pipeline currently only has a collapsed max score, not per-site donor/acceptor "
            f"loss/gain breakdown, so this is a coarse check."
        )
        return PVS1Result("Moderate", "REVIEW_REQUIRED", reasons, caveats, True)
    if max_score is not None and max_score >= SPLICEAI_MEDIUM:
        reasons.append(f"SpliceAI max delta score={max_score:.2f} corroborates predicted splice disruption")

    rescue, rescue_notes = _rescue_signal(e)
    caveats.extend(rescue_notes)
    if rescue:
        return PVS1Result("Moderate", "APPLIED", reasons, caveats, False)

    if e.is_loftee_hc is False:
        caveats.append("LOFTEE: low-confidence LoF call")
        return PVS1Result("Moderate", "APPLIED", reasons, caveats, False)
    if e.is_loftee_hc is None and native_loss is None:
        caveats.append("Neither LOFTEE confidence nor SpliceAI score available — review recommended")
        return PVS1Result("Moderate", "REVIEW_REQUIRED", reasons, caveats, True)

    nmd_predicted, nmd_reason = _evaluate_nmd(e)
    reasons.append(nmd_reason)

    if nmd_predicted is True:
        return PVS1Result(_downgrade_strength("Very_Strong", downgrade), "APPLIED", reasons, caveats, False)
    if nmd_predicted is False:
        frac, frac_reason = _protein_fraction_lost(e)
        reasons.append(frac_reason)
        base = "Strong" if (frac is not None and frac > 0.10) else "Moderate"
        return PVS1Result(_downgrade_strength(base, downgrade), "APPLIED", reasons, caveats, frac is None)

    caveats.append("NMD status undetermined — capped at Moderate pending review")
    return PVS1Result("Moderate", "REVIEW_REQUIRED", reasons, caveats, True)


def _evaluate_splice_region(e: PVS1Evidence, mech_established: Optional[bool]) -> PVS1Result:
    """
    Non-canonical splice-region variants now use SpliceAI's max delta score
    to move beyond a flat 'Supporting' when strong disruption is predicted
    despite the non-canonical position.
    """
    reasons = [f"Consequence: {e.consequence} — non-canonical splice-region variant"]
    if mech_established is False:
        return PVS1Result(None, "NOT_APPLICABLE", reasons + ["LoF not established as disease mechanism"], [], False)

    max_score = e.max_spliceai
    if max_score is None:
        return PVS1Result(
            "Supporting", "APPLIED", reasons,
            ["No SpliceAI score available — defaulting to Supporting"],
            False,
        )

    reasons.append(f"SpliceAI max delta score={max_score:.2f}")
    if max_score >= SPLICEAI_MEDIUM:
        return PVS1Result(
            "Moderate", "APPLIED", reasons,
            [f"SpliceAI score ≥{SPLICEAI_MEDIUM} despite non-canonical position — elevated from default Supporting"],
            False,
        )
    return PVS1Result(
        "Supporting", "APPLIED", reasons,
        [f"SpliceAI score <{SPLICEAI_MEDIUM} — consistent with uncertain/low splice impact"],
        False,
    )


# ---------------------------------------------------------------------------
# Branch: start_lost — always separate from LOFTEE and from splice evidence
# ---------------------------------------------------------------------------
def _evaluate_start_lost(e: PVS1Evidence, mech_established: Optional[bool]) -> PVS1Result:
    reasons = [f"Consequence: {e.consequence} (initiation codon variant)"]
    caveats = [
        "LOFTEE does not classify start_lost — is_loftee_hc is not applicable and was "
        "NOT used to derive this result. SpliceAI is also not relevant to this mechanism."
    ]
    if mech_established is False:
        return PVS1Result(None, "NOT_APPLICABLE", reasons + ["LoF not established as disease mechanism"], caveats, False)

    caveats.append(
        "Downstream in-frame alternate start codon and alternate-transcript-rescue data "
        "are not available in this pipeline (needs CDS sequence lookup). Capped at Moderate "
        "pending manual/curated review of alternate-ATG rescue per ClinGen initiation-codon guidance."
    )
    return PVS1Result("Moderate", "REVIEW_REQUIRED", reasons, caveats, True)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
def _route(e: PVS1Evidence) -> PVS1Result:
    mech_established, downgrade, mech_reasons = _evaluate_lof_mechanism(e)

    if e.consequence in NMD_TRUNCATING_CONSEQUENCES:
        result = _evaluate_nonsense_frameshift(e, mech_established, downgrade)
    elif e.consequence in CANONICAL_SPLICE_CONSEQUENCES:
        result = _evaluate_canonical_splice(e, mech_established, downgrade)
    elif e.consequence in INITIATION_CODON_CONSEQUENCES:
        result = _evaluate_start_lost(e, mech_established)
    elif e.consequence in SPLICE_REGION_CONSEQUENCES:
        result = _evaluate_splice_region(e, mech_established)
    elif e.consequence in CNV_CONSEQUENCES:
        result = PVS1Result(
            None, "REVIEW_REQUIRED",
            [f"Consequence: {e.consequence}"],
            ["Structural/CNV PVS1 pathway not yet implemented in this rule engine — "
             "flagged for manual review rather than defaulted."],
            True,
        )
    else:
        result = PVS1Result(None, "NOT_APPLICABLE", [f"{e.consequence} is not a LoF consequence type"], [], False)

    result.reasons = mech_reasons + result.reasons
    return result


# ---------------------------------------------------------------------------
# Main agent function — output contract unchanged
# ---------------------------------------------------------------------------
def agent2_consequence(state: VariantState) -> dict:
    e = _extract_evidence(state)
    logger.info(f" Evaluating {e.variant_id} ({e.gene}) — {e.consequence}")

    criteria_p: dict = {}
    criteria_b: dict = {}

    if e.consequence not in ALL_PVS1_ELIGIBLE_CONSEQUENCES and e.consequence not in CNV_CONSEQUENCES:
        return {
            "agent_evidence": {
                "agent2": {
                    "criteria_pathogenic": {},
                    "criteria_benign": {},
                    "evidence_notes": (
                        f"PVS1 not applicable: {e.consequence} is not a loss-of-function consequence type."
                    ),
                    "citations": PVS1_CITATIONS,
                    "confidence": "HIGH",
                }
            }
        }

    result = _route(e)

    if result.strength:
        criteria_p["PVS1"] = result.strength

    note_lines = [f"PVS1 status: {result.status}" + (f" ({result.strength})" if result.strength else "")]
    note_lines.extend(result.reasons)
    if result.caveats:
        note_lines.append("Caveats: " + "; ".join(result.caveats))
    if result.review_required:
        note_lines.append(
            "REVIEW_REQUIRED: evidence was insufficient for a fully confident automated call "
            "on at least one decision branch — recommend manual review."
        )
    evidence_notes = " | ".join(note_lines)
    confidence = "LOW" if result.status == "REVIEW_REQUIRED" else "HIGH"

    logger.info(f"[agent2] {e.variant_id}: P={criteria_p} status={result.status} conf={confidence}")

    return {
        "agent_evidence": {
            "agent2": {
                "criteria_pathogenic": criteria_p,
                "criteria_benign": criteria_b,
                "evidence_notes": evidence_notes,
                "citations": PVS1_CITATIONS,
                "confidence": confidence,
            }
        }
    }
