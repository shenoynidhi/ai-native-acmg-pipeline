"""
src/pipeline/runner.py

Entry point for a full pipeline run.

Architecture — two-pass design matching how vep_runner + post_process work:

  Pass 1 (VEP pass) — one graph invocation for the whole VCF:
      validate → detect_annotation → vep_runner (whole VCF at once)
      → prefilter → phasing → post_process
      post_process parses ALL variants from the VEP TSV and stores them
      in state["parsed_variants"]. The graph continues with the first
      variant through agents/debate/HPO as a side effect, but we only
      care about extracting parsed_variants from this result.

  Pass 2 (per-variant pass) — one graph invocation per parsed variant:
      Each variant's fields are pre-populated from parsed_variants.
      vep_already_annotated=True → detect_annotation routes directly to
      prefilter, skipping VEP entirely (VEP already ran in pass 1).
      → prefilter → phasing → post_process (no-op, TSV already parsed)
      → agents → debate → HPO → report_stub → END

  After all pass-2 invocations complete:
      generate_reports() is called once with all completed states.

Usage:
    from src.pipeline.runner import run_session
    result = run_session(
        session_id       = "abc12345",
        proband_vcf_path = "/workspace/data/acmg-pipeline/data/output/abc12345/proband.vcf.gz",
        genome_build     = "GRCh38",
        clinical_notes   = "Patient presents with seizures and developmental delay.",
    )
    print(result["report_paths"])
"""

import copy
import logging
from pathlib import Path
from typing import Optional

from src.pipeline.graph import VARIANT_GRAPH
from src.pipeline.state import build_initial_state, VariantState
from src.pipeline.nodes.report_generator import generate_reports
from src.config import OUTPUT_DIR, REPORT_CONFIG
from src.utils.logging_config import (
    configure_pipeline_logging,
    log_session_header,
    log_session_footer,
    ProgressCallback
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pass 1 — run VEP on the whole VCF, extract parsed_variants
# ---------------------------------------------------------------------------

def _run_vep_pass(
    session_id:        str,
    proband_vcf_path:  str,
    genome_build:      str,
    clinical_notes:    Optional[str],
    patient_hpo_terms: list,
    parent1_vcf_path:  Optional[str],
    parent2_vcf_path:  Optional[str],
    proband_bam_path:  Optional[str],
    parent1_bam_path:  Optional[str],
    parent2_bam_path:  Optional[str],
    proband_sex:       str,
    case_database_csv: Optional[str],
) -> tuple[list[VariantState], str]:
    """
    Invoke the graph once to run VEP annotation on the full VCF.

    Returns:
        (parsed_variants, annotated_tsv_path)
        parsed_variants — list of VariantState dicts from post_process_node
        annotated_tsv   — path to VEP TSV (re-used by pass-2 states)
    """
    state = build_initial_state(
        session_id        = session_id,
        proband_vcf_path  = proband_vcf_path,
        genome_build      = genome_build,
        clinical_notes    = clinical_notes,
        patient_hpo_terms = patient_hpo_terms,
        parent1_vcf_path  = parent1_vcf_path,
        parent2_vcf_path  = parent2_vcf_path,
        proband_bam_path  = proband_bam_path,
        parent1_bam_path  = parent1_bam_path,
        parent2_bam_path  = parent2_bam_path,
        proband_sex       = proband_sex,
        case_database_csv = case_database_csv,
    )

    logger.info(f"[{session_id}] Pass 1 — running VEP on full VCF")
    result = VARIANT_GRAPH.invoke(state)

    parsed_variants = result.get("parsed_variants") or []
    annotated_tsv   = result.get("annotated_tsv", "")

    if not parsed_variants:
        logger.warning(
            f"[{session_id}] Pass 1 complete but no parsed_variants in state. "
            f"Check post_process_node returns 'parsed_variants' in its dict."
        )
    else:
        logger.info(
            f"[{session_id}] Pass 1 complete — "
            f"{len(parsed_variants)} variants parsed from VEP TSV"
        )

    return parsed_variants, annotated_tsv


# ---------------------------------------------------------------------------
# Pass 2 — run agents + debate + HPO for one pre-annotated variant
# ---------------------------------------------------------------------------

def _run_variant_pass(
    variant_state:     VariantState,
    session_id:        str,
    proband_vcf_path:  str,
    genome_build:      str,
    annotated_tsv:     str,
    clinical_notes:    Optional[str],
    patient_hpo_terms: list,
    parent1_vcf_path:  Optional[str],
    parent2_vcf_path:  Optional[str],
    proband_bam_path:  Optional[str],
    parent1_bam_path:  Optional[str],
    parent2_bam_path:  Optional[str],
    proband_sex:       str,
    case_database_csv: Optional[str],
) -> VariantState:
    """
    Run the full graph for one already-annotated variant.

    vep_already_annotated=True tells detect_annotation_node to skip VEP.
    annotated_tsv is pre-set so post_process_node can still find the TSV
    if it needs to (though it will skip re-parsing since variant fields
    are already populated from pass 1).
    """
    variant_id = variant_state.get("variant_id", "?")

    # Start from a fresh base state so session fields are clean
    state = build_initial_state(
        session_id        = session_id,
        proband_vcf_path  = proband_vcf_path,
        genome_build      = genome_build,
        clinical_notes    = clinical_notes,
        patient_hpo_terms = patient_hpo_terms,
        parent1_vcf_path  = parent1_vcf_path,
        parent2_vcf_path  = parent2_vcf_path,
        proband_bam_path  = proband_bam_path,
        parent1_bam_path  = parent1_bam_path,
        parent2_bam_path  = parent2_bam_path,
        proband_sex       = proband_sex,
        case_database_csv = case_database_csv,
    )

    # Overlay all variant-specific fields from post_process_node output
    # Skip session-level fields that build_initial_state already set correctly
    _session_keys = {
        "session_id", "proband_vcf_path", "genome_build",
        "parent1_vcf_path", "parent2_vcf_path", "trio_mode",
        "proband_sex", "clinical_notes", "patient_hpo_terms",
        "warnings", "case_database_csv",
    }
    for key, value in variant_state.items():
        if key not in _session_keys:
            state[key] = value

    # Tell the graph: VEP already ran, skip it
    state["vep_already_annotated"] = True
    state["annotated_tsv"]         = annotated_tsv

    try:
        logger.info(f"[{session_id}] Pass 2 — processing {variant_id}")
        state["warnings"] = []
        result = VARIANT_GRAPH.invoke(state)
        logger.info(
            f"[{session_id}] {variant_id} → "
            f"{result.get('final_classification', 'VUS')} "
            f"(confidence: {result.get('confidence', '?')})"
        )
        return result

    except Exception as e:
        logger.error(
            f"[{session_id}] Graph failed for {variant_id}: {e}",
            exc_info=True,
        )
        # Degrade gracefully — don't drop the variant from the report
        state["final_classification"] = state.get("final_classification") or "VUS"
        state["confidence"]           = "LOW"
        state["evidence_summary"]     = "Pipeline error — variant could not be fully evaluated."
        state["warnings"]             = list(state.get("warnings") or []) + [
            f"Pipeline error: {e}"
        ]
        return state


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_session(
    session_id:        str,
    proband_vcf_path:  str,
    proband_bam_path:  str,
    genome_build:      str,
    clinical_notes:    Optional[str]  = None,
    patient_hpo_terms: Optional[list] = None,
    parent1_vcf_path:  Optional[str]  = None,
    parent2_vcf_path:  Optional[str]  = None,
    parent1_bam_path:  Optional[str]       = None,
    parent2_bam_path:  Optional[str]       = None,
    proband_sex:       str            = "unknown",
    output_formats:    Optional[list] = None,
    case_database_csv: Optional[str]  = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """
    Run the full ACMG pipeline for a patient VCF.

    Args:
        session_id:        Unique identifier for this analysis run.
        proband_vcf_path:  Absolute path to proband VCF (or VCF.gz).
        genome_build:      "GRCh38" or "GRCh37" — supplied by user at submission.
        clinical_notes:    Free-text clinical notes (optional).
        patient_hpo_terms: Pre-parsed HPO term list (optional, skips NLP if provided).
        parent1_vcf_path:  Maternal VCF for trio mode (optional).
        parent2_vcf_path:  Paternal VCF for trio mode (optional).
        proband_sex:       "male" | "female" | "unknown".
        output_formats:    Subset of ["xlsx", "tsv", "html"]. Default: all three.
        case_database_csv: Optional path to user case database CSV for PS4 evaluation.
        progress_callback: Optional callback for real-time progress updates (API layer).

    Returns:
        {
            "session_id":       str,
            "variant_count":    int,
            "report_paths":     {"xlsx": Path, "tsv": Path, "html": Path},
            "completed_states": [VariantState, ...]
        }
    """
    # Configure logging (suppresses cosmetic warnings, preserves actionable info)
    configure_pipeline_logging(level=logging.INFO, suppress_warnings=True)

    if output_formats is None:
        output_formats = ["xlsx", "tsv", "html"]

    work_dir = OUTPUT_DIR / session_id
    work_dir.mkdir(parents=True, exist_ok=True)

    # Log session header
    log_session_header(session_id, genome_build)

    # Create detailed progress emitter
    from src.pipeline.progress_emitter import DetailedProgressEmitter
    progress = DetailedProgressEmitter(progress_callback, total_variants=1)  # Will update after VEP

    # Emit progress: session started
    if progress_callback:
        progress_callback.update('initialization', 0.05, 'Session initialized', session_id=session_id)

    # ── Pass 1: VEP annotation + variant parsing ─────────────────────────────
    progress.vep_starting()

    parsed_variants, annotated_tsv = _run_vep_pass(
        session_id        = session_id,
        proband_vcf_path  = proband_vcf_path,
        genome_build      = genome_build,
        clinical_notes    = clinical_notes,
        patient_hpo_terms = patient_hpo_terms or [],
        parent1_vcf_path  = parent1_vcf_path,
        parent2_vcf_path  = parent2_vcf_path,
        proband_bam_path = proband_bam_path,
        parent1_bam_path = parent1_bam_path,
        parent2_bam_path = parent2_bam_path,
        proband_sex       = proband_sex,
        case_database_csv = case_database_csv,
    )

    # Update progress emitter with actual variant count
    progress.total_variants = len(parsed_variants)
    progress.vep_complete(len(parsed_variants))

    if not parsed_variants:
        logger.warning(f"[{session_id}] No variants to process — aborting.")
        if progress_callback:
            progress_callback.update('complete', 1.0, 'No variants found', status='complete')
        return {
            "session_id":       session_id,
            "variant_count":    0,
            "report_paths":     {},
            "completed_states": [],
        }

    # ── Pass 2: agents + debate + HPO per variant ────────────────────────────
    completed_states = []
    total = len(parsed_variants)

    for i, variant_state in enumerate(parsed_variants, start=1):
        variant_id = variant_state.get("variant_id", f"variant_{i}")
        gene = variant_state.get("gene", "unknown")
        logger.info(f"[{session_id}] Variant {i}/{total}: {variant_id}")

        # Emit progress: starting variant
        progress.variant_starting(variant_id, gene, i)

        result = _run_variant_pass(
            variant_state     = variant_state,
            session_id        = session_id,
            proband_vcf_path  = proband_vcf_path,
            genome_build      = genome_build,
            annotated_tsv     = annotated_tsv,
            clinical_notes    = clinical_notes,
            patient_hpo_terms = patient_hpo_terms or [],
            parent1_vcf_path  = parent1_vcf_path,
            parent2_vcf_path  = parent2_vcf_path,
            proband_sex       = proband_sex,
            proband_bam_path = proband_bam_path,
            parent1_bam_path = parent1_bam_path,
            parent2_bam_path = parent2_bam_path,
            case_database_csv = case_database_csv,
        )
        completed_states.append(result)

        # Emit progress: variant complete
        classification = result.get("final_classification", "VUS")
        progress.variant_complete(variant_id, gene, classification)

    logger.info(
        f"[{session_id}] All {len(completed_states)} variants processed — "
        f"generating reports"
    )

    # Emit progress: generating reports
    progress.generating_reports()

    # ── Reports: one call, full variant list ─────────────────────────────────
    # genome_build is session-specific (user-supplied), so override the static
    # REPORT_CONFIG branding on a shallow copy — never mutate the global.
    rc = copy.copy(REPORT_CONFIG)
    rc.genome_build = genome_build

    report_paths = generate_reports(
        states        = completed_states,
        session_id    = session_id,
        output_dir    = work_dir / "reports",
        formats       = output_formats,
        report_config = rc,
    )

    # Log session footer
    log_session_footer(len(completed_states), report_paths)

    # Emit progress: complete
    progress.complete(len(completed_states))
    if progress_callback:
        progress_callback.update(
            'complete',
            1.0,
            f'Classification complete - {len(completed_states)} variants',
            status='complete',
            variant_count=len(completed_states),
            report_paths={k: str(v) for k, v in report_paths.items()}
        )

    return {
        "session_id":       session_id,
        "variant_count":    len(completed_states),
        "report_paths":     report_paths,
        "completed_states": completed_states,
    }


