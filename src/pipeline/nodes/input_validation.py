"""
src/pipeline/nodes/input_validation.py

Input Validation Node — Phase 3
Validates the input VCF before any processing begins.

Checks:
  1. File exists and is readable
  2. File is a valid VCF (has ##fileformat header)
  3. Genome build in VCF header matches config
  4. At least one variant record present
  5. FORMAT/GT column present (required for zygosity)
  6. File is bgzipped + tabix-indexed if .vcf.gz (warns if not)

Outputs (added to VariantState):
    validation_passed  — True if all hard checks pass
    warnings           — non-fatal issues appended (e.g. missing index)

Raises:
    ValueError         — on any hard failure (stops the pipeline immediately)
"""

import gzip
import logging
from pathlib import Path

from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)

# Consequence types that are hard failures vs soft warnings
_HARD_FAIL_EXTENSIONS = {".bcf"}   # BCF not supported yet — VEP call needs VCF


def _open_vcf(path: Path):
    """Return a file handle that works for both .vcf and .vcf.gz."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def validate_input_node(state: VariantState) -> dict:
    """
    Validate the proband VCF before the pipeline runs.

    Hard failures raise ValueError — the graph stops immediately.
    Soft issues are appended to warnings and the pipeline continues.
    """
    session_id = state["session_id"]
    warnings   = list(state.get("warnings", []))
    vcf_path   = Path(state["proband_vcf_path"])

    logger.info(f"[{session_id}] Validating input VCF: {vcf_path}")

    # ------------------------------------------------------------------
    # 1. File exists
    # ------------------------------------------------------------------
    if not vcf_path.exists():
        raise ValueError(
            f"[{session_id}] Input VCF not found: {vcf_path}"
        )

    # ------------------------------------------------------------------
    # 2. Extension check
    # ------------------------------------------------------------------
    suffix = vcf_path.suffix.lower()
    if suffix in _HARD_FAIL_EXTENSIONS:
        raise ValueError(
            f"[{session_id}] BCF format not supported. "
            f"Convert with: bcftools view -O z {vcf_path} > output.vcf.gz"
        )
    if suffix not in (".vcf", ".gz"):
        raise ValueError(
            f"[{session_id}] Unrecognised file extension '{suffix}'. "
            f"Expected .vcf or .vcf.gz"
        )

    # ------------------------------------------------------------------
    # 3. Parse header and first data lines
    # ------------------------------------------------------------------
    has_fileformat  = False
    has_gt_format   = False
    genome_build_vcf = None
    data_line_count  = 0
    header_line      = None

    try:
        with _open_vcf(vcf_path) as fh:
            for line in fh:
                line = line.rstrip("\n")

                if line.startswith("##fileformat=VCF"):
                    has_fileformat = True

                elif line.startswith("##reference") or line.startswith("##genome_build"):
                    low = line.lower()
                    if "grch38" in low or "hg38" in low:
                        genome_build_vcf = "GRCh38"
                    elif "grch37" in low or "hg19" in low:
                        genome_build_vcf = "GRCh37"

                elif line.startswith("##FORMAT=<ID=GT"):
                    has_gt_format = True

                elif line.startswith("#CHROM"):
                    header_line = line
                    # Stop reading header — count data lines next
                    continue

                elif not line.startswith("#"):
                    data_line_count += 1
                    if data_line_count >= 3:
                        # Enough to confirm file has variants — stop reading
                        break

    except (OSError, gzip.BadGzipFile) as e:
        raise ValueError(
            f"[{session_id}] Cannot read VCF (corrupt or wrong format): {e}"
        )

    # ------------------------------------------------------------------
    # 4. Hard checks on parsed content
    # ------------------------------------------------------------------
    if not has_fileformat:
        raise ValueError(
            f"[{session_id}] VCF missing '##fileformat=VCFv4.x' header line. "
            f"File may be corrupt or not a VCF."
        )

    if data_line_count == 0:
        raise ValueError(
            f"[{session_id}] VCF contains no variant records. "
            f"Check that the file is not empty and is not header-only."
        )

    if header_line is None:
        raise ValueError(
            f"[{session_id}] VCF missing #CHROM header line."
        )

    # ------------------------------------------------------------------
    # 5. Soft checks — append to warnings, do not raise
    # ------------------------------------------------------------------
    if not has_gt_format:
        warnings.append(
            "VCF_WARN: No FORMAT/GT field found. "
            "Zygosity-based criteria (PM3, BS2, BP2) will be skipped."
        )
        logger.warning(f"[{session_id}] No GT field in VCF — zygosity checks disabled.")

    # Genome build mismatch check
    expected_build = state.get("genome_build", "GRCh38")
    if genome_build_vcf and genome_build_vcf != expected_build:
        warnings.append(
            f"VCF_WARN: VCF header declares {genome_build_vcf} but "
            f"pipeline configured for {expected_build}. "
            f"Verify the correct build is set in PipelineConfig."
        )
        logger.warning(
            f"[{session_id}] Genome build mismatch: "
            f"VCF={genome_build_vcf}, config={expected_build}"
        )

    # Index check for .vcf.gz
    if vcf_path.suffix == ".gz":
        tbi = Path(str(vcf_path) + ".tbi")
        csi = Path(str(vcf_path) + ".csi")
        if not tbi.exists() and not csi.exists():
            warnings.append(
                f"VCF_WARN: No .tbi or .csi index found for {vcf_path.name}. "
                f"Some tools may be slower. Index with: tabix -p vcf {vcf_path}"
            )
            logger.warning(f"[{session_id}] No tabix index found for {vcf_path.name}")

    logger.info(f"[{session_id}] Input validation passed. Warnings: {len(warnings)}")

    return {
        "validation_passed": True,
        "warnings":          warnings,
    }
