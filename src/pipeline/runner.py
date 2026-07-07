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
from multiprocessing import cpu_count
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing

from src.pipeline.graph import VARIANT_GRAPH, PASS2_GRAPH
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

# Variant-level parallelization: use 16 workers on 32-core system
# Leave headroom for system processes and avoid memory pressure
# NOTE: If running Celery with --concurrency=2 or higher, reduce this to 8
#       to avoid CPU overload (2 VCFs × 8 variants = 16 total, fits 32 cores)
NUM_VARIANT_WORKERS = min(28, max(1, cpu_count() - 2))


def _is_running_in_celery():
    """Check if we're running inside a Celery worker (daemon process)."""
    try:
        return multiprocessing.current_process().daemon
    except:
        return False


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
    progress_callback: Optional[ProgressCallback] = None,
) -> VariantState:
    """
    Run the PASS2_GRAPH for one already-annotated variant.

    CRITICAL OPTIMIZATION: Uses PASS2_GRAPH which starts directly at run_agents,
    skipping all preprocessing (validate_input, detect_annotation, strip_alts,
    prefilter, VEP, phasing, post_process).

    This eliminates 8+ minutes of redundant overhead per variant!

    Variant fields are already populated from Pass 1 (post_process output),
    so we just need to run agents → debate → HPO → report.
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

    # These flags are no longer needed since PASS2_GRAPH doesn't run preprocessing
    # But keep them for backwards compatibility with any code that checks them
    state["vep_already_annotated"] = True
    state["annotated_tsv"]         = annotated_tsv

    try:
        logger.info(f"[{session_id}] Pass 2 — processing {variant_id}")
        state["warnings"] = []

        # USE PASS2_GRAPH INSTEAD OF VARIANT_GRAPH!
        # This skips validate_input → detect_annotation → strip_alts → prefilter → VEP → phasing → post_process
        # and starts directly at run_agents (saves 8+ minutes per variant!)
        result = PASS2_GRAPH.invoke(state)
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
# Parallel variant processing wrapper (for multiprocessing.Pool)
# ---------------------------------------------------------------------------

def _process_single_variant_worker(args: tuple) -> VariantState:
    """
    Worker function for parallel variant processing.

    This function is called by multiprocessing.Pool workers.
    Each worker processes one variant independently.

    Args:
        args: Tuple of (variant_state, session_params)
              session_params contains all the kwargs needed by _run_variant_pass

    Returns:
        Completed VariantState after agents + debate + HPO + report
    """
    variant_state, session_params = args

    # Unpack session parameters
    return _run_variant_pass(
        variant_state=variant_state,
        **session_params
    )


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
    # Process variants in parallel (16 workers on 32-core system)
    # This gives 16× speedup on Pass 2 (the slowest part of the pipeline)

    total = len(parsed_variants)
    logger.info(
        f"[{session_id}] Processing {total} variants in parallel "
        f"({NUM_VARIANT_WORKERS} workers)"
    )

    # Build session parameters dict (same for all variants)
    # NOTE: progress_callback cannot be pickled, so we don't pass it to workers
    # Progress updates happen in the main process as results come back
    session_params = {
        "session_id": session_id,
        "proband_vcf_path": proband_vcf_path,
        "genome_build": genome_build,
        "annotated_tsv": annotated_tsv,
        "clinical_notes": clinical_notes,
        "patient_hpo_terms": patient_hpo_terms or [],
        "parent1_vcf_path": parent1_vcf_path,
        "parent2_vcf_path": parent2_vcf_path,
        "proband_bam_path": proband_bam_path,
        "parent1_bam_path": parent1_bam_path,
        "parent2_bam_path": parent2_bam_path,
        "proband_sex": proband_sex,
        "case_database_csv": case_database_csv,
        "progress_callback": None,  # Cannot be pickled for multiprocessing
    }

    # Prepare arguments for worker pool (variant_state, session_params pairs)
    worker_args = [(variant_state, session_params) for variant_state in parsed_variants]

    # Process variants in parallel
    # Use ThreadPoolExecutor if in Celery (daemon), otherwise use multiprocessing.Pool
    completed_states = []

    if _is_running_in_celery():
        # Running in Celery worker (daemon process) - use threads instead of processes
        logger.info(f"[{session_id}] Running in Celery - using ThreadPoolExecutor")

        with ThreadPoolExecutor(max_workers=NUM_VARIANT_WORKERS) as executor:
            # Submit all variants
            future_to_idx = {
                executor.submit(_process_single_variant_worker, args): i
                for i, args in enumerate(worker_args, start=1)
            }

            # Collect results as they complete
            for future in as_completed(future_to_idx):
                i = future_to_idx[future]
                try:
                    result = future.result(timeout=600)  # 10 min timeout per variant

                    variant_id = result.get("variant_id", f"variant_{i}")
                    gene = result.get("gene", "unknown")
                    classification = result.get("final_classification", "VUS")

                    logger.info(
                        f"[{session_id}] Variant {i}/{total}: {variant_id} → {classification}"
                    )

                    # Emit progress
                    progress.variant_complete(variant_id, gene, classification)

                    completed_states.append(result)

                except Exception as e:
                    logger.error(f"[{session_id}] Variant {i} failed: {e}")
                    # Create error state
                    completed_states.append({
                        "variant_id": f"variant_{i}",
                        "final_classification": "VUS",
                        "confidence": "LOW",
                        "evidence_summary": f"Processing error: {e}",
                    })

    else:
        # Not in Celery - use multiprocessing.Pool for true parallel execution
        logger.info(f"[{session_id}] Using multiprocessing.Pool for parallel processing")

        from multiprocessing import Pool

        with Pool(processes=NUM_VARIANT_WORKERS) as pool:
            # Use imap for better progress tracking (results come back as they complete)
            for i, result in enumerate(pool.imap(_process_single_variant_worker, worker_args), start=1):
                variant_id = result.get("variant_id", f"variant_{i}")
                gene = result.get("gene", "unknown")
                classification = result.get("final_classification", "VUS")

                logger.info(
                    f"[{session_id}] Variant {i}/{total}: {variant_id} → {classification}"
                )

                # Emit progress
                progress.variant_complete(variant_id, gene, classification)

                completed_states.append(result)

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

    # Finalize token usage tracking
    try:
        from src.utils.token_tracker import finalize_session
        finalize_session(session_id)
        logger.info(f"[{session_id}] Token usage summary saved")
    except Exception as e:
        logger.warning(f"[{session_id}] Failed to finalize token tracking: {e}")

    return {
        "session_id":       session_id,
        "variant_count":    len(completed_states),
        "report_paths":     report_paths,
        "completed_states": completed_states,
    }


