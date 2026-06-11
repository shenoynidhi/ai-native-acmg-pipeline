"""
src/pipeline/nodes/vep_runner.py

VEP Runner Node — Phase 4
Shells out to VEP 115.2 to annotate a filtered VCF.
Now fully build-aware: GRCh38 and GRCh37 both supported.
"""

import logging
import subprocess
from pathlib import Path
from typing import List

from src.config import (
    VEP_BINARY,
    VEP_PERL,
    VEP_ROOT,
    OUTPUT_DIR,
    get_database_paths,
)
from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)

_PLUGINS_DIR = VEP_ROOT / "Plugins"

_DBNSFP_FIELDS = [
    "REVEL_score",
    "CADD_phred",
    "Polyphen2_HDIV_score",
    "SIFT_score",
    "phyloP100way_vertebrate",
    "GERP++_RS",
    "MutationTaster_pred",
    "MetaSVM_score",
]

# LOFTEE uses different GERP mechanism per build
# GRCh38: bigwig (.bw)   GRCh37: tabix-indexed txt.gz
_LOFTEE_GERP_FLAG = {
    "GRCh38": "gerp_bigwig",
    "GRCh37": "gerp_tabix",
}


def _build_vep_command(
    input_vcf: Path,
    output_tsv: Path,
    genome_build: str,
) -> List[str]:
    """Build the full VEP command for the given genome build."""

    db = get_database_paths(genome_build)
    build_upper = genome_build.upper()  # "GRCH38" / "GRCH37"
    assembly    = "GRCh37" if build_upper == "GRCH37" else "GRCh38"
    cache_key   = "vep_cache_grch37" if build_upper == "GRCH37" else "vep_cache"

    loftee_gerp_flag = _LOFTEE_GERP_FLAG.get(assembly, "gerp_bigwig")

    cmd = [
        str(VEP_PERL),
        str(VEP_BINARY),

        # Cache / offline
        "--cache",
        "--offline",
        "--dir",           str(VEP_ROOT),
        "--dir_plugins",   str(_PLUGINS_DIR),
        "--species",       "homo_sapiens",
        "--assembly",      assembly,
        "--cache_version", "115",

        # Input / output
        "--input_file",    str(input_vcf),
        "--output_file",   str(output_tsv),
        "--force_overwrite",
        "--tab",
        "--no_stats",

        # Transcript / annotation flags
        "--canonical",
        "--symbol",
        "--numbers",
        "--hgvs",
        "--hgvsg",
        "--everything",

        # dbNSFP plugin
        "--plugin", (
            f"dbNSFP,{db['dbnsfp']},"
            + ",".join(_DBNSFP_FIELDS)
        ),

        # SpliceAI plugin
        "--plugin", (
            f"SpliceAI,"
            f"snv={db['spliceai_snv']},"
            f"indel={db['spliceai_indel']}"
        ),

        # LOFTEE plugin
        "--plugin", (
            f"LoF,"
            f"loftee_path:{db['loftee_dir']},"
            f"human_ancestor_fa:{db['loftee_human_ancestor_fa']},"
            f"{loftee_gerp_flag}:{db['loftee_gerp']}"
        ),

        # ClinVar custom annotation
        "--custom", (
            f"file={db['clinvar_vcf']},"
            "short_name=ClinVar,"
            "format=vcf,"
            "type=exact,"
            "coords=0,"
            "fields=CLNSIG%CLNREVSTAT%CLNDN%CLNACC"
        ),
    ]
    return cmd


def vep_runner_node(state: VariantState) -> dict:
    """
    Run VEP on the filtered VCF and write annotated TSV to the session work dir.
    Reads genome_build from state (defaults to GRCh38).
    """
    session_id   = state["session_id"]
    genome_build = state.get("genome_build", "GRCh38")
    warnings     = list(state.get("warnings", []))

    input_vcf = state.get("filtered_vcf") or state.get("proband_vcf_path")
    if not input_vcf:
        raise ValueError(f"[{session_id}] vep_runner: no input VCF path in state.")
    input_vcf = Path(input_vcf)
    if not input_vcf.exists():
        raise FileNotFoundError(f"[{session_id}] vep_runner: input VCF not found: {input_vcf}")

    work_dir   = OUTPUT_DIR / session_id / "vep_out"
    work_dir.mkdir(parents=True, exist_ok=True)
    output_tsv = work_dir / f"{session_id}_vep.tsv"

    cmd = _build_vep_command(input_vcf, output_tsv, genome_build)
    logger.info(f"[{session_id}] Running VEP ({genome_build}) on {input_vcf.name}")
    logger.debug(f"[{session_id}] VEP command:\n  " + " \\\n  ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=7200,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"[{session_id}] VEP timed out after 2 hours on {input_vcf}")

    if proc.stderr:
        for line in proc.stderr.splitlines():
            ll = line.lower()
            if any(kw in ll for kw in ("error", "failed", "die", "fatal")):
                logger.error(f"[{session_id}] VEP stderr: {line}")
                warnings.append(f"VEP_ERROR: {line}")
            elif "warn" in ll or "could not" in ll:
                logger.warning(f"[{session_id}] VEP stderr: {line}")
                warnings.append(f"VEP_WARN: {line}")
            else:
                logger.debug(f"[{session_id}] VEP: {line}")

    if proc.returncode != 0:
        raise RuntimeError(
            f"[{session_id}] VEP exited with code {proc.returncode}.\n"
            f"Last stderr:\n{proc.stderr[-2000:]}"
        )

    if not output_tsv.exists():
        raise RuntimeError(
            f"[{session_id}] VEP completed but output TSV not found: {output_tsv}"
        )

    logger.info(f"[{session_id}] VEP complete → {output_tsv}")

    return {
        "annotated_tsv":         str(output_tsv),
        "vep_already_annotated": False,
        "warnings":              warnings,
    }
