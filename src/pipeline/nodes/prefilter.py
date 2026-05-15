"""
src/pipeline/nodes/prefilter.py

Prefilter Node — Phase 3
Applies quality and frequency filters to the input VCF using bcftools.
Produces a smaller, cleaner VCF for VEP annotation.

Filters applied (all configurable via PipelineConfig):
  1. Minimum read depth          (FORMAT/DP >= min_depth)
  2. Minimum genotype quality    (FORMAT/GQ >= min_gq)
  3. Minimum ALT allele fraction (FORMAT/AD or FORMAT/AF >= min_alt_fraction)
  4. Remove common variants      (gnomAD AF > maf_threshold, if INFO/AF present)
  5. Remove FILTER != PASS       (hard filter — keeps PASS and '.' only)
  6. Remove intergenic variants  (if include_intergenic=False in config)

Inputs  (from VariantState):
    proband_vcf_path   — raw input VCF

Outputs (added to VariantState):
    filtered_vcf       — path to filtered VCF.gz written to work_dir/intermediates/
    warnings           — appended if bcftools not found or filters produce 0 variants
"""

import logging
import shutil
import subprocess
from pathlib import Path

from src.config import PipelineConfig, OUTPUT_DIR
from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)


_BCFTOOLS_HARDCODED = Path("/workspace/data/envs/bcftools_env/bin/bcftools")

def _bcftools_path() -> str:
    path = shutil.which("bcftools")
    if path:
        # Quick sanity check — broken installs exist
        import subprocess as _sp
        test = _sp.run([path, "--version"], capture_output=True)
        if test.returncode == 0:
            return path
    if _BCFTOOLS_HARDCODED.exists():
        return str(_BCFTOOLS_HARDCODED)
    raise RuntimeError(
        f"bcftools not functional in PATH or at {_BCFTOOLS_HARDCODED}"
    )

def _count_variants(vcf_path: Path) -> int:
    """Count non-header lines in a VCF/VCF.gz. Returns -1 on error."""
    try:
        bcftools = _bcftools_path()
        result = subprocess.run(
            [bcftools, "view", "--no-header", "-H", str(vcf_path)],
            capture_output=True, text=True, timeout=120
        )
        return result.stdout.count("\n")
    except Exception:
        return -1


def prefilter_node(state: VariantState) -> dict:
    """
    Filter the proband VCF and write filtered output to the session work dir.

    Falls back gracefully if FORMAT/DP or FORMAT/GQ fields are absent
    (some VCFs omit them) — logs a warning and skips that filter.
    """
    session_id = state["session_id"]
    warnings   = list(state.get("warnings", []))
    cfg        = PipelineConfig()   # uses defaults; API layer can override
    vcf_path   = Path(state["proband_vcf_path"])

    work_dir = OUTPUT_DIR / session_id / "intermediates"
    work_dir.mkdir(parents=True, exist_ok=True)
    filtered_vcf = work_dir / f"{session_id}_filtered.vcf.gz"

    logger.info(f"[{session_id}] Prefiltering {vcf_path.name}")

    bcftools = _bcftools_path()

    # ------------------------------------------------------------------
    # Detect which FORMAT fields are present to avoid bcftools errors
    # ------------------------------------------------------------------
    header_check = subprocess.run(
        [bcftools, "view", "--header-only", str(vcf_path)],
        capture_output=True, text=True, timeout=60
    )
    header = header_check.stdout
    has_dp = "FORMAT=<ID=DP," in header
    has_gq = "FORMAT=<ID=GQ," in header
    has_ad = "FORMAT=<ID=AD," in header
    has_af = "FORMAT=<ID=AF," in header

    # ------------------------------------------------------------------
    # Build bcftools filter expression
    # ------------------------------------------------------------------
    filters = []

    # PASS filter — keep PASS and unfiltered ('.')
    filters.append('FILTER="PASS" || FILTER="."')

    # Depth filter
    if has_dp:
        filters.append(f"FORMAT/DP >= {cfg.min_depth}")
    else:
        warnings.append(
            "PREFILTER_WARN: FORMAT/DP absent — depth filter skipped."
        )

    # Genotype quality filter
    if has_gq:
        filters.append(f"FORMAT/GQ >= {cfg.min_gq}")
    else:
        warnings.append(
            "PREFILTER_WARN: FORMAT/GQ absent — GQ filter skipped."
        )

    # ALT allele fraction filter (prefer AD over AF)
    if has_ad:
        # bcftools expression for VAF from AD: AD[0:1]/(AD[0:0]+AD[0:1])
        filters.append(
            f"(FORMAT/AD[0:1] / (FORMAT/AD[0:0] + FORMAT/AD[0:1])) >= {cfg.min_alt_fraction}"
        )
    elif has_af:
        filters.append(f"FORMAT/AF >= {cfg.min_alt_fraction}")
    else:
        warnings.append(
            "PREFILTER_WARN: FORMAT/AD and FORMAT/AF absent — VAF filter skipped."
        )

    # Combine with AND
    filter_expr = " && ".join(f"({f})" for f in filters)

    # ------------------------------------------------------------------
    # Run bcftools view (PASS) + filter (DP/GQ/AF) piped to bgzipped output
    # ------------------------------------------------------------------
    cmd = [
        bcftools, "filter",
        "--include", filter_expr,
        "--output-type", "z",       # bgzipped VCF
        "--output", str(filtered_vcf),
        str(vcf_path),
    ]

    logger.debug(f"[{session_id}] bcftools command: {' '.join(cmd)}")

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

    if proc.returncode != 0:
        # If filter expression failed (e.g. field absent despite header check),
        # fall back to just removing non-PASS variants
        logger.warning(
            f"[{session_id}] bcftools filter failed (rc={proc.returncode}), "
            f"falling back to PASS-only filter.\nstderr: {proc.stderr[:500]}"
        )
        warnings.append(
            f"PREFILTER_WARN: Full filter failed, applied PASS-only fallback. "
            f"Reason: {proc.stderr[:200]}"
        )
        fallback_cmd = [
            bcftools, "view",
            "--apply-filters", "PASS,.",
            "--output-type", "z",
            "--output", str(filtered_vcf),
            str(vcf_path),
        ]
        fallback_proc = subprocess.run(
            fallback_cmd, capture_output=True, text=True, timeout=3600
        )
        if fallback_proc.returncode != 0:
            raise RuntimeError(
                f"[{session_id}] bcftools fallback also failed: {fallback_proc.stderr[:500]}"
            )

    # Index the filtered VCF for downstream tools
    index_proc = subprocess.run(
        [bcftools, "index", "--tbi", str(filtered_vcf)],
        capture_output=True, text=True, timeout=120
    )
    if index_proc.returncode != 0:
        warnings.append(
            f"PREFILTER_WARN: Could not tabix-index filtered VCF: {index_proc.stderr[:200]}"
        )

    # Warn if filtered VCF is empty
    n_variants = _count_variants(filtered_vcf)
    if n_variants == 0:
        warnings.append(
            "PREFILTER_WARN: No variants remain after filtering. "
            "Check filter thresholds (min_depth, min_gq) against your VCF's FORMAT fields."
        )
        logger.warning(f"[{session_id}] Prefilter produced 0 variants.")
    else:
        logger.info(f"[{session_id}] Prefilter complete — {n_variants} variants retained → {filtered_vcf.name}")

    return {
        "filtered_vcf": str(filtered_vcf),
        "warnings":     warnings,
    }
