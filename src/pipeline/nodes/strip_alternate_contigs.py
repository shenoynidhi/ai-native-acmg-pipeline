"""
src/pipeline/nodes/strip_alternate_contigs.py

Strip Alternate Contigs Node — Phase 1b
Removes alternate contigs (ALT loci, patches, fix scaffolds) from input VCF before annotation.

Alternate contigs (NT_*, NW_*, KI*, GL*) are:
  - Alternative representations of complex regions in GRCh38
  - NOT targeted by exome capture kits
  - NOT used in clinical ACMG classification (only chr1-22, X, Y, MT)
  - NOT annotated in gnomAD/ClinVar/HGMD
  - Cause VEP warnings and slow down parsing (zygosity extraction bottleneck)

This node runs BEFORE prefilter to ensure clean VCF throughout the pipeline.

Inputs  (from VariantState):
    proband_vcf_path   — raw input VCF (may contain 500+ alternate contigs)

Outputs (added to VariantState):
    cleaned_vcf        — path to VCF with alternate contigs removed
    warnings           — appended if bcftools fails or no variants remain
"""

import logging
import shutil
import subprocess
from pathlib import Path

from src.config import OUTPUT_DIR
from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)

# Alternate contig prefixes to exclude (GRCh38)
# Based on NCBI/Heng Li recommendations for clinical pipelines
_ALTERNATE_CONTIG_PREFIXES = [
    "NT_",      # RefSeq alternate loci (NT_187361.1, etc.)
    "NW_",      # RefSeq patches/fix scaffolds
    "KI",       # KI270* alternate loci
    "GL",       # GL000* unlocalized/unplaced sequences
    "chrUn",    # Unlocalized sequences (if chr-prefixed)
]

_BCFTOOLS_HARDCODED = Path("/workspace/data/envs/bcftools_env/bin/bcftools")


def _bcftools_path() -> str:
    """Get bcftools binary path (prefer PATH, fallback to hardcoded)."""
    path = shutil.which("bcftools")
    if path:
        # Quick sanity check
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
    """Count non-header lines in a VCF. Returns -1 on error."""
    try:
        bcftools = _bcftools_path()
        result = subprocess.run(
            [bcftools, "view", "--no-header", "-H", str(vcf_path)],
            capture_output=True, text=True, timeout=120
        )
        return result.stdout.count("\n")
    except Exception:
        return -1


def strip_alternate_contigs_node(state: VariantState) -> dict:
    """
    Remove alternate contigs from the input VCF and write cleaned output.

    Uses bcftools to exclude chromosomes matching alternate contig patterns.
    Runs before prefilter to ensure VEP and downstream nodes work on clean data.
    """
    session_id = state["session_id"]
    warnings   = list(state.get("warnings", []))
    vcf_path   = Path(state["proband_vcf_path"])

    work_dir = OUTPUT_DIR / session_id / "intermediates"
    work_dir.mkdir(parents=True, exist_ok=True)
    cleaned_vcf = work_dir / f"{session_id}_no_alts.vcf.gz"

    logger.info(f"[{session_id}] Stripping alternate contigs from {vcf_path.name}")

    bcftools = _bcftools_path()

    # Count original variants
    original_count = _count_variants(vcf_path)
    if original_count > 0:
        logger.info(f"[{session_id}] Original VCF: {original_count} variants")

    # Build bcftools targets expression to EXCLUDE alternate contigs
    # Format: ^NT_*,NW_*,KI*,GL*,chrUn* (^ means exclude)
    exclude_targets = ",".join(f"{prefix}*" for prefix in _ALTERNATE_CONTIG_PREFIXES)
    targets_expr = f"^{exclude_targets}"

    # Run bcftools view with --targets to exclude alternate contigs
    # NOTE: --targets works on VCF without index; --regions requires .tbi
    cmd = [
        bcftools, "view",
        "--targets", targets_expr,
        "--output-type", "z",       # bgzipped VCF
        "--output", str(cleaned_vcf),
        str(vcf_path),
    ]

    logger.debug(f"[{session_id}] bcftools command: {' '.join(cmd)}")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"[{session_id}] bcftools alternate contig stripping timed out after 10 minutes"
        )

    if proc.returncode != 0:
        # Fallback: if --targets fails, just copy the original VCF
        logger.warning(
            f"[{session_id}] bcftools --targets failed (rc={proc.returncode}), "
            f"using original VCF. stderr: {proc.stderr[:500]}"
        )
        warnings.append(
            f"STRIP_ALTS_WARN: bcftools filtering failed, using original VCF. "
            f"Reason: {proc.stderr[:200]}"
        )
        # Symlink or copy original VCF as fallback
        if cleaned_vcf.exists():
            cleaned_vcf.unlink()
        shutil.copy2(vcf_path, cleaned_vcf)
    else:
        # Success - log how many variants were removed
        cleaned_count = _count_variants(cleaned_vcf)
        if cleaned_count == 0:
            warnings.append(
                "STRIP_ALTS_WARN: No variants remain after removing alternate contigs. "
                "Check if input VCF only contains alternate contigs."
            )
            logger.warning(f"[{session_id}] Cleaned VCF has 0 variants!")
        elif cleaned_count > 0 and original_count > 0:
            removed = original_count - cleaned_count
            logger.info(
                f"[{session_id}] Removed {removed} alternate contig variants "
                f"({cleaned_count} standard chromosome variants retained)"
            )
        else:
            logger.info(f"[{session_id}] Cleaned VCF ready → {cleaned_vcf.name}")

    # Index the cleaned VCF for downstream tools
    index_proc = subprocess.run(
        [bcftools, "index", "--tbi", str(cleaned_vcf)],
        capture_output=True, text=True, timeout=120
    )
    if index_proc.returncode != 0:
        warnings.append(
            f"STRIP_ALTS_WARN: Could not index cleaned VCF: {index_proc.stderr[:200]}"
        )

    return {
        "cleaned_vcf": str(cleaned_vcf),
        "warnings": warnings,
    }
