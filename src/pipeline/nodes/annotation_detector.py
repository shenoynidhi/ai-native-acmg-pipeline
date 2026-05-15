"""
src/pipeline/nodes/annotation_detector.py

Annotation Detector Node — Phase 3
Checks whether the input VCF already has VEP annotation (CSQ or ANN fields).
If it does, the graph skips vep_runner_node entirely.

Sets:
    vep_already_annotated — True if CSQ/ANN INFO fields found in VCF header
"""

import gzip
import logging
from pathlib import Path

from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)

# VEP writes CSQ; SnpEff writes ANN; some pipelines write both
_ANNOTATION_MARKERS = ("##INFO=<ID=CSQ,", "##INFO=<ID=ANN,", "##INFO=<ID=vep,")


def _open_vcf(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def detect_annotation_node(state: VariantState) -> dict:
    """
    Scan VCF header lines for CSQ/ANN INFO markers.
    Stops reading as soon as the first data line is reached (efficient).
    """
    session_id = state["session_id"]
    vcf_path   = Path(state.get("filtered_vcf") or state["proband_vcf_path"])

    logger.info(f"[{session_id}] Checking for existing VEP annotation in {vcf_path.name}")

    already_annotated = False
    try:
        with _open_vcf(vcf_path) as fh:
            for line in fh:
                if not line.startswith("#"):
                    break   # past header — stop
                if any(line.startswith(m) for m in _ANNOTATION_MARKERS):
                    already_annotated = True
                    break
    except Exception as e:
        logger.warning(f"[{session_id}] Could not read VCF for annotation check: {e}")

    if already_annotated:
        logger.info(f"[{session_id}] VEP annotation detected — skipping VEP runner.")
    else:
        logger.info(f"[{session_id}] No existing annotation found — VEP will run.")

    return {"vep_already_annotated": already_annotated}
