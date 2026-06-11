"""
src/pipeline/nodes/vep_runner.py

VEP Runner Node — Phase 4
Shells out to VEP 115.2 (in the vep conda env) to annotate a filtered VCF.

Inputs  (from VariantState):
    session_id          — used to find the work directory
    filtered_vcf        — path set by prefilter_node (or proband_vcf_path if no prefilter)

Outputs (added to VariantState):
    annotated_tsv       — path to the VEP tab-delimited output file
    vep_already_annotated — set False (we just ran VEP, so downstream skip-check is clear)
    warnings            — any VEP stderr warnings appended here

Plugins enabled (all data already present in /workspace/data/.vep/):
    dbNSFP    — REVEL, CADD_phred, PolyPhen2, SIFT, phyloP100way, GERP++
    SpliceAI  — masked SNV + indel scores
    LoF       — LOFTEE HC/LC classification
    ClinVar   — CLNSIG, CLNREVSTAT, CLNDN, CLNACC (via --custom)
    gnomAD    — exome AFs via --af_gnomad (built into VEP cache)
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
)
from src.pipeline.state import VariantState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths derived from VEP_ROOT — all confirmed present in plugin test
# ---------------------------------------------------------------------------
_DBNSFP      = VEP_ROOT / "dbnsfp"  / "dbNSFP5.3.1a_grch38.gz"
_SPLICEAI_SNV   = VEP_ROOT / "spliceai" / "spliceai_scores.masked.snv.hg38.vcf.gz"
_SPLICEAI_INDEL = VEP_ROOT / "spliceai" / "spliceai_scores.masked.indel.hg38.vcf.gz"
_LOFTEE_DIR     = VEP_ROOT / "loftee"
_LOFTEE_ANC_FA  = VEP_ROOT / "loftee"  / "human_ancestor.fa.gz"
_LOFTEE_GERP    = VEP_ROOT / "loftee"  / "gerp_conservation_scores.homo_sapiens.GRCh38.bw"
_CLINVAR_VCF    = VEP_ROOT / "clinvar" / "clinvar.vcf.gz"
_PLUGINS_DIR    = VEP_ROOT / "Plugins"

# dbNSFP fields to extract — maps directly to VariantState fields in post_process_node
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


def _build_vep_command(input_vcf: Path, output_tsv: Path) -> List[str]:
    """
    Build the full VEP command list.
    Structured so each logical group is easy to audit and extend.
    """
    cmd = [
        str(VEP_PERL),
        str(VEP_BINARY),

        # --- Cache / offline mode ---
        "--cache",
        "--offline",
        "--dir",          str(VEP_ROOT),
        "--dir_plugins",  str(_PLUGINS_DIR),
        "--species",      "homo_sapiens",
        "--assembly",     "GRCh38",
        "--cache_version","115",

        # --- Input / output ---
        "--input_file",   str(input_vcf),
        "--output_file",  str(output_tsv),
        "--force_overwrite",
        "--tab",                  # tab-delimited output (easier to parse than VCF CSQ)
        "--no_stats",             # skip HTML stats file — not needed in pipeline

        # --- Transcript selection ---
        "--canonical",            # annotate canonical transcript flag
        "--symbol",               # include HGNC gene symbol
        "--numbers",              # exon/intron numbers (e.g. 3/23)
        "--hgvs",                 # HGVSc and HGVSp
        "--hgvsg",                # HGVSg (genomic)

        # --- Filtering (keep everything, prefilter_node already applied quality filters) ---
        "--everything",           # enable all annotation flags above in one switch
                                  # (overrides individual flags but we keep them explicit
                                  #  for clarity; --everything is not redundant here as
                                  #  it also enables regulatory, protein domains, etc.)

        # --- Plugins ---
        "--plugin", (
            f"dbNSFP,{_DBNSFP},"
            + ",".join(_DBNSFP_FIELDS)
        ),
        "--plugin", (
            f"SpliceAI,"
            f"snv={_SPLICEAI_SNV},"
            f"indel={_SPLICEAI_INDEL}"
        ),
        "--plugin", (
            f"LoF,"
            f"loftee_path:{_LOFTEE_DIR},"
            f"human_ancestor_fa:{_LOFTEE_ANC_FA},"
            f"gerp_bigwig:{_LOFTEE_GERP}"
        ),

        # --- Custom annotation: ClinVar ---
        "--custom", (
            f"file={_CLINVAR_VCF},"
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

    Reads:
        state["session_id"]       — to locate work dir
        state["filtered_vcf"]     — path to input (set by prefilter_node)
                                    falls back to proband_vcf_path if prefilter skipped
    Writes:
        annotated_tsv             — path to VEP output TSV
        vep_already_annotated     — False (we just ran, clear the skip flag)
        warnings                  — any stderr lines appended
    """
    session_id = state["session_id"]
    warnings   = list(state.get("warnings", []))

    # Resolve input path: prefer filtered VCF, fall back to raw proband VCF
    input_vcf = state.get("filtered_vcf") or state.get("proband_vcf_path")
    if not input_vcf:
        raise ValueError(f"[{session_id}] vep_runner: no input VCF path in state.")
    input_vcf = Path(input_vcf)
    if not input_vcf.exists():
        raise FileNotFoundError(f"[{session_id}] vep_runner: input VCF not found: {input_vcf}")

    # Output TSV path
    work_dir    = OUTPUT_DIR / session_id / "vep_out"
    work_dir.mkdir(parents=True, exist_ok=True)
    output_tsv  = work_dir / f"{session_id}_vep.tsv"

    # Build and log the command
    cmd = _build_vep_command(input_vcf, output_tsv)
    logger.info(f"[{session_id}] Running VEP on {input_vcf.name}")
    logger.debug(f"[{session_id}] VEP command:\n  " + " \\\n  ".join(cmd))

    # Execute
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=7200,   # 2-hour hard limit; a full exome takes ~20-40 min
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"[{session_id}] VEP timed out after 2 hours on {input_vcf}")

    # Always log stderr — VEP writes progress + warnings there
    if proc.stderr:
        for line in proc.stderr.splitlines():
            if any(kw in line.lower() for kw in ("error", "failed", "die", "fatal")):
                logger.error(f"[{session_id}] VEP stderr: {line}")
                warnings.append(f"VEP_ERROR: {line}")
            elif "warn" in line.lower() or "could not" in line.lower():
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
        "annotated_tsv":        str(output_tsv),
        "vep_already_annotated": False,
        "warnings":             warnings,
    }
