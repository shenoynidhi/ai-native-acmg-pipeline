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

    # Use awk to filter alternate contigs (more reliable than bcftools --targets with globs)
    # Build awk pattern to exclude alternate contig prefixes
    exclude_patterns = "|".join([f"^{prefix}" for prefix in _ALTERNATE_CONTIG_PREFIXES])

    logger.debug(f"[{session_id}] Filtering alternate contigs with pattern: {exclude_patterns}")

    # Create temp uncompressed VCF first
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.vcf', delete=False, dir=work_dir) as tmp:
        tmp_vcf = Path(tmp.name)

    try:
        # Extract header
        logger.debug(f"[{session_id}] Extracting VCF header...")
        header_proc = subprocess.run(
            [bcftools, "view", "-h", str(vcf_path)],
            capture_output=True, text=True, timeout=120, check=True
        )

        # Filter body with awk (exclude alternate contigs)
        logger.debug(f"[{session_id}] Filtering variants...")
        body_proc = subprocess.run(
            [bcftools, "view", "-H", str(vcf_path)],
            capture_output=True, text=True, timeout=600, check=True
        )

        # Write filtered VCF
        with open(tmp_vcf, 'w') as f:
            # Write header
            f.write(header_proc.stdout)

            # Filter and write body (exclude lines starting with alternate contig prefixes)
            for line in body_proc.stdout.splitlines():
                # Check if line starts with any excluded prefix
                chrom = line.split('\t')[0] if '\t' in line else line.split()[0]
                if not any(chrom.startswith(prefix) for prefix in _ALTERNATE_CONTIG_PREFIXES):
                    f.write(line + '\n')

        # Compress filtered VCF
        logger.debug(f"[{session_id}] Compressing filtered VCF...")
        compress_proc = subprocess.run(
            [bcftools, "view", "-O", "z", "-o", str(cleaned_vcf), str(tmp_vcf)],
            capture_output=True, text=True, timeout=300, check=True
        )

        # Clean up temp file
        tmp_vcf.unlink()

        proc = compress_proc  # For compatibility with existing error handling

    except subprocess.TimeoutExpired:
        if tmp_vcf.exists():
            tmp_vcf.unlink()
        raise RuntimeError(
            f"[{session_id}] Alternate contig filtering timed out"
        )
    except Exception as e:
        # Clean up temp file on error
        if tmp_vcf.exists():
            tmp_vcf.unlink()

        # Fallback: copy original VCF
        logger.warning(
            f"[{session_id}] Alternate contig filtering failed: {e}, "
            f"using original VCF."
        )
        warnings.append(
            f"STRIP_ALTS_WARN: Filtering failed, using original VCF. Reason: {str(e)[:200]}"
        )
        if cleaned_vcf.exists():
            cleaned_vcf.unlink()
        shutil.copy2(vcf_path, cleaned_vcf)

    # Log results if filtering succeeded
    if cleaned_vcf.exists():
        cleaned_count = _count_variants(cleaned_vcf)
        if cleaned_count == 0:
            warnings.append(
                "STRIP_ALTS_WARN: No variants remain after removing alternate contigs. "
                "Check if input VCF only contains alternate contigs."
            )
            logger.warning(f"[{session_id}] Cleaned VCF has 0 variants!")
        elif cleaned_count > 0 and original_count > 0:
            removed = original_count - cleaned_count
            if removed > 0:
                logger.info(
                    f"[{session_id}] Removed {removed} alternate contig variants "
                    f"({cleaned_count} standard chromosome variants retained)"
                )
            else:
                logger.info(f"[{session_id}] No alternate contigs found - all {cleaned_count} variants retained")
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

