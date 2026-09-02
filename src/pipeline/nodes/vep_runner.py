"""
src/pipeline/nodes/vep_runner.py

VEP Runner Node — Phase 4
Shells out to VEP 115.2 to annotate a filtered VCF.
Now fully build-aware: GRCh38 and GRCh37 both supported.
"""

import logging
import subprocess
import shutil
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
_LOFTEE_DIR = VEP_ROOT / "loftee"

_DBNSFP_FIELDS = [
    "Ensembl_transcriptid",  # Transcript IDs for matching multi-transcript scores
    "REVEL_score",
    "CADD_phred",
    "Polyphen2_HDIV_score",
    "SIFT_score",
    "phyloP100way_vertebrate",
    "GERP++_RS",
    "MutationTaster_pred",
    "MetaSVM_score",
]

# LOFTEE GERP parameter - use gerp_file for both builds (works with .tsv.gz and .txt.gz)
# Note: gerp_bigwig (.bw) is also valid but requires newer LOFTEE version
_LOFTEE_GERP_FLAG = {
    "GRCh38": "gerp_file",
    "GRCh37": "gerp_file",
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

    # Reference FASTA (required for HGVSc/HGVSp generation)
    # get_database_paths() returns "reference_fasta" for both builds
    reference_fasta = db.get("reference_fasta")

    loftee_gerp_flag = _LOFTEE_GERP_FLAG.get(assembly, "gerp_bigwig")

    cmd = [
        str(VEP_PERL),
        str(VEP_BINARY),

        # Cache / offline
        "--cache",
        "--offline",
        "--dir",           str(VEP_ROOT),
        # Use Plugins/ directory for .pm files (includes LoF.pm, dbNSFP.pm, SpliceAI.pm)
        # loftee/ directory contains helper scripts (.pl files) referenced by LoF.pm
        "--dir_plugins",   str(_PLUGINS_DIR),
        "--species",       "homo_sapiens",
        "--assembly",      assembly,
        "--cache_version", "115",
        "--total_length",

        # Input / output
        "--input_file",    str(input_vcf),
        "--output_file",   str(output_tsv),
        "--force_overwrite",
        "--tab",
        "--no_stats",

        # Multithreading - use 30 cores (leave 2 for system on 32-core instance)
        "--fork",          "30",

        # Transcript / annotation flags
        "--canonical",
        "--symbol",
        "--numbers",
        "--hgvs",
        "--hgvsg",
        "--af_gnomad",  # Add gnomAD allele frequencies from VEP cache
        "--biotype",    # Add transcript biotype (protein_coding vs lncRNA) - CRITICAL for filtering
        "--tsl",        # Transcript support level
        "--appris",     # APPRIS principal isoform annotation
        "--variant_class",  # SNV, insertion, deletion, etc.
        "--allele_number",
        # NOTE: Removed --everything flag (causes large output, 20% slowdown)
        # We add back only the critical flags needed for ACMG classification
        # NOTE: Add "--merged" after installing RefSeq cache with:
        #   vep_install -a cf -s homo_sapiens -y GRCh38 --REFSEQ --CACHEDIR /workspace/data/.vep
        #   vep_install -a cf -s homo_sapiens -y GRCh37 --REFSEQ --CACHEDIR /workspace/data/.vep

        # dbNSFP plugin - scores for missense, splice, frameshift variants
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

        # gnomAD custom annotation (population frequencies from merged VCF)
        # Field list differs between builds: v4.1 (GRCh38) has AF_ami/AF_mid/AF_remaining
        # while v2.1.1 (GRCh37) uses AF_oth instead of AF_remaining and lacks AF_ami/AF_mid
    ]

    if build_upper == "GRCH37":
        # GRCh37 v2.1.1 fields
        cmd.append("--custom")
        cmd.append(
            f"file={db['gnomad_vcf']},"
            "short_name=gnomAD,"
            "format=vcf,"
            "type=exact,"
            "coords=0,"
            "fields=AF%AF_afr%AF_amr%AF_asj%AF_eas%AF_fin%AF_nfe%AF_oth%AF_sas%nhomalt"
        )
    else:
        # GRCh38 v4.1 fields
        cmd.append("--custom")
        cmd.append(
            f"file={db['gnomad_vcf']},"
            "short_name=gnomAD,"
            "format=vcf,"
            "type=exact,"
            "coords=0,"
            "fields=AF%AF_afr%AF_ami%AF_amr%AF_asj%AF_eas%AF_fin%AF_mid%AF_nfe%AF_remaining%AF_sas%nhomalt"
        )

    cmd.extend([
        # ClinVar custom annotation
        "--custom", (
            f"file={db['clinvar_vcf']},"
            "short_name=ClinVar,"
            "format=vcf,"
            "type=exact,"
            "coords=0,"
            "fields=CLNSIG%CLNREVSTAT%CLNDN%CLNACC"
        ),
    ])

    # Add --fasta only if reference file exists (required for complete HGVSc/HGVSp)
    if reference_fasta and Path(reference_fasta).exists():
        cmd.extend(["--fasta", str(reference_fasta)])
    else:
        logger.warning(
            f"Reference FASTA not found at {reference_fasta}. "
            "HGVSc/HGVSp may be incomplete for some variants."
        )

    return cmd


def vep_runner_node(state: VariantState) -> dict:
    """
    Run VEP on the filtered VCF and write annotated TSV to the session work dir.
    Reads genome_build from state (defaults to GRCh38).
    """
    session_id   = state["session_id"]
    genome_build = state.get("genome_build", "GRCh38")
    warnings     = list(state.get("warnings", []))

    # Check for filtered VCF first (from prefilter node), then cleaned VCF, then original
    filtered_vcf_path = state.get("filtered_vcf")
    cleaned_vcf_path  = state.get("cleaned_vcf")
    original_vcf_path = state.get("proband_vcf_path")

    # DEBUG: Log what's in state
    logger.debug(
        f"[{session_id}] VEP state check: "
        f"filtered_vcf={filtered_vcf_path}, cleaned_vcf={cleaned_vcf_path}, "
        f"proband_vcf_path={original_vcf_path}"
    )

    if filtered_vcf_path:
        input_vcf = Path(filtered_vcf_path)
        logger.info(f"[{session_id}] ✓ Using filtered VCF from prefilter: {input_vcf.name}")
    elif cleaned_vcf_path:
        input_vcf = Path(cleaned_vcf_path)
        logger.info(f"[{session_id}] ✓ Using cleaned VCF (alternate contigs removed): {input_vcf.name}")
    elif original_vcf_path:
        input_vcf = Path(original_vcf_path)
        logger.warning(
            f"[{session_id}] ⚠ Using original VCF (no filtered/cleaned VCF in state!): {input_vcf.name}"
        )
    else:
        raise ValueError(f"[{session_id}] vep_runner: no input VCF path in state.")

    if not input_vcf.exists():
        raise FileNotFoundError(f"[{session_id}] vep_runner: input VCF not found: {input_vcf}")

    # Count variants in input VCF
    try:
        import subprocess as _sp
        bcftools = shutil.which("bcftools") or "/workspace/data/envs/bcftools_env/bin/bcftools"
        count_result = _sp.run(
            [bcftools, "view", "--no-header", "-H", str(input_vcf)],
            capture_output=True, text=True, timeout=120
        )
        if count_result.returncode == 0:
            n_variants = count_result.stdout.count("\n")
        else:
            logger.warning(f"[{session_id}] Failed to count variants: {count_result.stderr[:200]}")
            n_variants = "?"
    except Exception as e:
        logger.warning(f"[{session_id}] Exception counting variants: {e}")
        n_variants = "?"

    work_dir   = OUTPUT_DIR / session_id / "vep_out"
    work_dir.mkdir(parents=True, exist_ok=True)
    output_tsv = work_dir / f"{session_id}_vep.tsv"

    cmd = _build_vep_command(input_vcf, output_tsv, genome_build)
    logger.info(f"[{session_id}] Running VEP ({genome_build}) on {input_vcf.name} ({n_variants} variants)")
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
            # Ignore plugin compilation warnings - they're harmless when using Docker VEP
            if "failed to compile plugin" in ll and "can't locate" in ll:
                logger.debug(f"[{session_id}] VEP plugin precompile warning (ignored): {line}")
                continue
            elif any(kw in ll for kw in ("error", "failed", "die", "fatal")):
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

