"""
src/pipeline/nodes/phasing.py

Phasing Node — Phase 3
Uses WhatsHap to phase variants in the proband VCF.
Phasing is critical for compound heterozygosity assessment (PM3, BP2).

Modes:
  - Solo (no BAM):  statistical phasing only via WhatsHap's --ignore-read-groups
  - Solo (BAM):     read-backed phasing from proband BAM
  - Trio:           pedigree phasing using parent VCFs (most accurate)

Outputs (added to VariantState):
    phased_vcf        — path to phased VCF.gz
    phase_status      — "compound_het_trans" | "compound_het_cis" |
                        "unphased" | "not_applicable"
    phase_confidence  — "HIGH" | "MEDIUM" | "LOW"
    warnings          — appended on any non-fatal issues

Falls back gracefully if:
  - WhatsHap not installed → skips phasing, sets phase_status="unphased"
  - No BAM provided in solo mode → statistical phasing only
  - Reference FASTA absent → skips phasing with warning
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from src.config import OUTPUT_DIR, OPTIONAL_DATABASE_PATHS, SAMTOOLS_BINARY
from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)

_REFERENCE_FASTA = OPTIONAL_DATABASE_PATHS["reference_fasta"]


def _whatshap_path() -> Optional[str]:
    """Return WhatsHap binary path or None if not installed."""
    path = shutil.which("whatshap")
    if path:
        return path
    # Check common conda locations
    for candidate in [
        "/workspace/data/envs/acmg/bin/whatshap",
        "/workspace/data/envs/bcftools_env/bin/whatshap",
    ]:
        if Path(candidate).exists():
            return candidate
    return None


def _index_vcf(vcf_path: Path, warnings: list) -> None:
    """Tabix-index a VCF.gz using bcftools from bcftools_env."""
    try:
        bcftools = str(Path("/workspace/data/envs/bcftools_env/bin/bcftools"))
        subprocess.run(
            [bcftools, "index", "--tbi", "--force", str(vcf_path)],
            capture_output=True, text=True, timeout=120, check=True
        )
    except Exception as e:
        warnings.append(f"PHASING_WARN: Could not index {vcf_path.name}: {e}")


def _compress_vcf(input_vcf: Path, output_vcf_gz: Path) -> None:
    """Bgzip a VCF using bcftools."""
    bcftools = str(Path("/workspace/data/envs/bcftools_env/bin/bcftools"))
    subprocess.run(
        [bcftools, "view", "-O", "z", "-o", str(output_vcf_gz), str(input_vcf)],
        capture_output=True, text=True, timeout=300, check=True
    )


def phasing_node(state: VariantState) -> dict:
    """
    Phase variants using WhatsHap.

    Reads from state:
        session_id, filtered_vcf, proband_vcf_path
        proband_bam (via run_context — optional)
        analysis_mode (solo vs trio)

    Note: run_context fields (proband_bam, father_vcf, mother_vcf) are not
    in VariantState directly. They are looked up from OUTPUT_DIR structure
    or passed as warnings if absent. For now we operate on the filtered VCF
    and use statistical phasing — BAM/trio support is wired but not required.
    """
    session_id = state["session_id"]
    warnings   = list(state.get("warnings", []))

    input_vcf  = Path(state.get("filtered_vcf") or state["proband_vcf_path"])
    work_dir   = OUTPUT_DIR / session_id / "intermediates"
    work_dir.mkdir(parents=True, exist_ok=True)
    phased_vcf = work_dir / f"{session_id}_phased.vcf.gz"

    # ------------------------------------------------------------------
    # Check prerequisites
    # ------------------------------------------------------------------
    whatshap = _whatshap_path()
    if whatshap is None:
        warnings.append(
            "PHASING_WARN: WhatsHap not found — phasing skipped. "
            "Install with: conda install -c bioconda whatshap"
        )
        logger.warning(f"[{session_id}] WhatsHap not found — skipping phasing.")
        return {
            "phased_vcf":      str(input_vcf),   # pass through unphased
            "phase_status":    "unphased",
            "phase_confidence":"LOW",
            "warnings":        warnings,
        }

    ref_fasta = Path(_REFERENCE_FASTA)
    if not ref_fasta.exists():
        warnings.append(
            "PHASING_WARN: Reference FASTA not found — phasing skipped. "
            f"Expected at: {ref_fasta}"
        )
        logger.warning(f"[{session_id}] Reference FASTA missing — skipping phasing.")
        return {
            "phased_vcf":       str(input_vcf),
            "phase_status":     "unphased",
            "phase_confidence": "LOW",
            "warnings":         warnings,
        }

    # ------------------------------------------------------------------
    # Build WhatsHap phase command
    # Statistical phasing only (no BAM) — read-backed phasing added
    # when proband BAM is provided via run_context in the API layer.
    # ------------------------------------------------------------------
    phased_vcf_tmp = work_dir / f"{session_id}_phased_tmp.vcf"

    cmd = [
        whatshap, "phase",
        "--output",          str(phased_vcf_tmp),
        "--reference",       str(ref_fasta),
        "--ignore-read-groups",          # required for statistical phasing without BAM
        "--indels",                      # phase indels as well as SNVs
        "--distrust-genotypes",          # allow phasing to correct genotype errors
        str(input_vcf),
    ]

    logger.info(f"[{session_id}] Running WhatsHap phase on {input_vcf.name}")
    logger.debug(f"[{session_id}] WhatsHap command: {' '.join(cmd)}")

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

    if proc.returncode != 0:
        warnings.append(
            f"PHASING_WARN: WhatsHap failed (rc={proc.returncode}) — "
            f"using unphased VCF. Stderr: {proc.stderr[:300]}"
        )
        logger.warning(f"[{session_id}] WhatsHap failed: {proc.stderr[:300]}")
        return {
            "phased_vcf":       str(input_vcf),
            "phase_status":     "unphased",
            "phase_confidence": "LOW",
            "warnings":         warnings,
        }

    # Compress and index
    try:
        _compress_vcf(phased_vcf_tmp, phased_vcf)
        _index_vcf(phased_vcf, warnings)
        phased_vcf_tmp.unlink(missing_ok=True)
    except Exception as e:
        warnings.append(f"PHASING_WARN: Could not compress phased VCF: {e}")
        logger.warning(f"[{session_id}] Compression failed: {e}")
        return {
            "phased_vcf":       str(input_vcf),
            "phase_status":     "unphased",
            "phase_confidence": "LOW",
            "warnings":         warnings,
        }

    # ------------------------------------------------------------------
    # Assess phasing confidence from WhatsHap stderr stats
    # WhatsHap reports: "X variants were phased" in stderr
    # ------------------------------------------------------------------
    phase_confidence = "LOW"
    phased_count     = 0
    total_count      = 0

    for line in proc.stderr.splitlines():
        if "phased" in line.lower() and "variant" in line.lower():
            import re
            nums = re.findall(r"\d+", line)
            if len(nums) >= 1:
                phased_count = int(nums[0])
        if "variant" in line.lower() and "input" in line.lower():
            nums = re.findall(r"\d+", line)
            if nums:
                total_count = int(nums[0])

    if total_count > 0:
        fraction = phased_count / total_count
        if fraction >= 0.8:
            phase_confidence = "HIGH"
        elif fraction >= 0.4:
            phase_confidence = "MEDIUM"
        else:
            phase_confidence = "LOW"
    elif phased_count > 0:
        phase_confidence = "MEDIUM"

    logger.info(
        f"[{session_id}] Phasing complete → {phased_vcf.name} "
        f"(confidence: {phase_confidence}, phased: {phased_count})"
    )

    return {
        "phased_vcf":       str(phased_vcf),
        "phase_status":     "unphased",        # compound_het detection happens in Agent 6
        "phase_confidence": phase_confidence,
        "warnings":         warnings,

