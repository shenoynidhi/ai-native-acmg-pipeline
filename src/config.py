"""
Central configuration for the ACMG pipeline.
All paths, thresholds, and settings live here.

All paths and binary locations are driven by environment variables so the
same codebase runs unchanged across:
  - Kubernetes pod (current dev environment)
  - Local Docker Compose (laptop development)
  - AWS ECS / EKS (production)

Environment variable precedence:
  1. Shell environment (set by Docker Compose or ECS task definition)
  2. .env file (local dev convenience)
  3. Hardcoded defaults (pod fallbacks for current dev stage)
"""

import os
from pathlib import Path
from typing import Dict
from pydantic import BaseModel
from dotenv import load_dotenv

# Load .env from project root — works on pod and locally.
# In Docker, env vars are injected directly so .env is ignored.
load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Base directories — all overridable via environment variables
# ---------------------------------------------------------------------------
# On pod:          /workspace/data/acmg-pipeline/data/*
# In Docker:       /data/*  (mounted volumes)
# On AWS:          /data/*  (EBS volume or S3 mount)

DATABASE_DIR  = Path(os.getenv("DATABASE_DIR",  "/workspace/data/acmg-pipeline/data/databases"))
CHROMADB_DIR  = Path(os.getenv("CHROMADB_DIR",  "/workspace/data/acmg-pipeline/data/chromadb"))
OUTPUT_DIR    = Path(os.getenv("OUTPUT_DIR",    "/workspace/data/acmg-pipeline/data/output"))
REFERENCE_DIR = Path(os.getenv("REFERENCE_DIR", "/workspace/data/acmg-pipeline/data/reference"))

# VEP cache + plugin data root
# On pod:    /workspace/data/.vep
# In Docker: /data/vep  (mounted from host)
VEP_ROOT = Path(os.getenv("VEP_DATA_DIR", "/workspace/data/.vep"))

# ---------------------------------------------------------------------------
# Binary paths — overridable so Docker containers use their own installs
# ---------------------------------------------------------------------------
# On pod:    conda env paths (hardcoded below as fallback)
# In Docker: /usr/bin/bcftools etc (installed in container image)

VEP_BINARY      = Path(os.getenv("VEP_BINARY",
    "/workspace/data/envs/vep/share/ensembl-vep-115.2-1/vep"))
VEP_PERL        = Path(os.getenv("VEP_PERL",
    "/workspace/data/envs/vep/bin/perl"))
BCFTOOLS_BINARY = Path(os.getenv("BCFTOOLS_BINARY",
    "/workspace/data/envs/bcftools_env/bin/bcftools"))
SAMTOOLS_BINARY = Path(os.getenv("SAMTOOLS_BINARY",
    "/workspace/data/envs/bcftools_env/bin/samtools"))
WHATSHAP_BINARY = Path(os.getenv("WHATSHAP_BINARY",
    "/workspace/data/envs/whatshap_env/bin/whatshap"))

# ---------------------------------------------------------------------------
# vLLM / LLM settings
# On pod:    vLLM on pod-b at 172.29.127.170:8000
# In Docker: set LLM_BASE_URL in docker-compose.yml or .env
# On AWS:    set LLM_BASE_URL to SageMaker endpoint or hosted vLLM instance
# ---------------------------------------------------------------------------
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "http://172.29.127.185:8000/v1")
LLM_MODEL:    str = os.getenv("LLM_MODEL",    "qwen2.5-14b")
LLM_API_KEY:  str = os.getenv("LLM_API_KEY",  "dummy")

# ---------------------------------------------------------------------------
# PipelineConfig — all tunable parameters for a single run
# ---------------------------------------------------------------------------

class PipelineConfig(BaseModel):
    """All configurable parameters for a pipeline run.
    
    Instantiate with defaults:   cfg = PipelineConfig()
    Override at runtime:         cfg = PipelineConfig(min_depth=15, genome_build="GRCh37")
    """

    # ---- LLM ----------------------------------------------------------------
    llm_base_url:   str   = LLM_BASE_URL
    llm_model:      str   = LLM_MODEL
    llm_api_key:    str   = LLM_API_KEY
    llm_temperature: float = 0.1
    llm_max_tokens:  int   = 1000

    # ---- Genome build -------------------------------------------------------
    genome_build: str = "GRCh38"   # or "GRCh37"

    # ---- Quality-filter thresholds (pre-filter node) ------------------------
    maf_threshold:      float = 0.01    # variants above this MAF are flagged
    min_depth:          int   = 10      # minimum read depth (FORMAT/DP)
    min_gq:             int   = 20      # minimum genotype quality (FORMAT/GQ)
    min_alt_fraction:   float = 0.20    # minimum ALT allele fraction (het calls)
    max_alt_fraction:   float = 0.80    # maximum ALT allele fraction (hom calls)
    include_intergenic: bool  = False   # drop intergenic variants
    include_synonymous: bool  = False   # drop synonymous variants

    # ---- ACMG evidence thresholds -------------------------------------------
    # Population frequency (BA1 / BS1)
    ba1_threshold:              float = 0.05    # stand-alone benign (>5 % in any population)
    bs1_threshold_recessive:    float = 0.005   # BS1 for AR genes
    bs1_threshold_dominant:     float = 0.0002  # BS1 for AD genes
    pm2_threshold:              float = 0.0001  # PM2: absent / very rare

    # In-silico predictors (PP3 / BP4)
    revel_pathogenic_threshold: float = 0.75
    revel_benign_threshold:     float = 0.15
    spliceai_high_threshold:    float = 0.80   # strong splice evidence
    spliceai_low_threshold:     float = 0.20   # weak / no splice evidence
    cadd_pathogenic_threshold:  int   = 20     # CADD PHRED score
    phylop_conservation_threshold: float = 2.5 # PhyloP100way

    # ---- Output settings ----------------------------------------------------
    output_classes:       str  = "all"   # "p_lp" | "p_lp_b_lb" | "all"
    output_formats:       list = ["xlsx", "tsv", "html"]
    include_evidence_tab: bool = True
    include_citations:    bool = True


# ---------------------------------------------------------------------------
# DATABASE_PATHS — canonical locations for every reference file
#
# Priority logic:
#   1. If the file already exists under VEP_ROOT  → use it directly
#   2. Otherwise point to DATABASE_DIR           → downloaded by setup_databases.sh
#
# Keys match the names used throughout the pipeline codebase.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Build-aware helper — resolves a VEP_ROOT-relative path for a given build
# ---------------------------------------------------------------------------

def _vep_path(subdir: str, build: str, filename: str) -> Path:
    """
    Returns VEP_ROOT / subdir / grch38_or_grch37 / filename.
    build must be "GRCh38" or "GRCh37".
    """
    folder = "grch37" if build.upper() == "GRCH37" else "grch38"
    return VEP_ROOT / subdir / folder / filename


# ---------------------------------------------------------------------------
# DATABASE_PATHS — required files, build-independent
# (all VEP plugin data is now in _vep_path(); only truly build-agnostic
#  files live here — HPO, ClinGen, HGNC, gnomAD constraint, ChromaDB)
# ---------------------------------------------------------------------------

DATABASE_PATHS: dict = {

    # VEP cache roots (both builds present)
    "vep_cache":          VEP_ROOT / "homo_sapiens" / "115_GRCh38",
    "vep_cache_grch37":   VEP_ROOT / "homo_sapiens" / "115_GRCh37",

    # gnomAD constraint (pLI/LOEUF) — build-independent gene-level file
    "gnomad_constraint":  DATABASE_DIR / "gnomad" / "gnomad.v2.1.1.lof_metrics.by_gene.txt",

    # HPO
    "hpo_obo":            DATABASE_DIR / "hpo" / "hp.obo",
    "hpo_annotations":    DATABASE_DIR / "hpo" / "phenotype.hpoa",

    # ClinGen
    "clingen_validity":   DATABASE_DIR / "clingen" / "gene_disease_validity.csv",

    # HGNC
    "hgnc":               DATABASE_DIR / "hgnc" / "hgnc_complete_set.txt",
}

# ---------------------------------------------------------------------------
# Build-aware DATABASE_PATHS — call get_database_paths(genome_build) at
# runtime to get the full merged dict for a specific build.
# ---------------------------------------------------------------------------

def get_database_paths(genome_build: str = "GRCh38") -> dict:
    """
    Returns DATABASE_PATHS merged with all build-specific plugin paths.
    Use this everywhere instead of DATABASE_PATHS directly when you need
    a VEP plugin path.

    genome_build: "GRCh38" or "GRCh37"
    """
    b = genome_build  # shorthand

    build_paths = {
        # --- ClinVar ---------------------------------------------------------
        "clinvar_vcf":        _vep_path("clinvar",  b, "clinvar.vcf.gz"),
        "clinvar_vcf_tbi":    _vep_path("clinvar",  b, "clinvar.vcf.gz.tbi"),

        # --- dbNSFP ----------------------------------------------------------
        "dbnsfp": (
            _vep_path("dbnsfp", b, "dbNSFP5.3.1a_grch37.gz")
            if b.upper() == "GRCH37"
            else _vep_path("dbnsfp", b, "dbNSFP5.3.1a_grch38.gz")
        ),
        "dbnsfp_tbi": (
            _vep_path("dbnsfp", b, "dbNSFP5.3.1a_grch37.gz.tbi")
            if b.upper() == "GRCH37"
            else _vep_path("dbnsfp", b, "dbNSFP5.3.1a_grch38.gz.tbi")
        ),

        # --- gnomAD tabbed ---------------------------------------------------
        "gnomad_tabbed": (
            _vep_path("gnomad", b, "gnomad.genomes.tabbed.tsv.gz")
            if b.upper() == "GRCH37"
            else _vep_path("gnomad", b, "gnomad.ch.genomesv3.tabbed.tsv.gz")
        ),
        "gnomad_tabbed_tbi": (
            _vep_path("gnomad", b, "gnomad.genomes.tabbed.tsv.gz.tbi")
            if b.upper() == "GRCH37"
            else _vep_path("gnomad", b, "gnomad.ch.genomesv3.tabbed.tsv.gz.tbi")
        ),

        # --- SpliceAI --------------------------------------------------------
        "spliceai_snv": (
            _vep_path("spliceai", b, "spliceai_scores.masked.snv.hg19.vcf.gz")
            if b.upper() == "GRCH37"
            else _vep_path("spliceai", b, "spliceai_scores.masked.snv.hg38.vcf.gz")
        ),
        "spliceai_snv_tbi": (
            _vep_path("spliceai", b, "spliceai_scores.masked.snv.hg19.vcf.gz.tbi")
            if b.upper() == "GRCH37"
            else _vep_path("spliceai", b, "spliceai_scores.masked.snv.hg38.vcf.gz.tbi")
        ),
        "spliceai_indel": (
            _vep_path("spliceai", b, "spliceai_scores.masked.indel.hg19.vcf.gz")
            if b.upper() == "GRCH37"
            else _vep_path("spliceai", b, "spliceai_scores.masked.indel.hg38.vcf.gz")
        ),
        "spliceai_indel_tbi": (
            _vep_path("spliceai", b, "spliceai_scores.masked.indel.hg19.vcf.gz.tbi")
            if b.upper() == "GRCH37"
            else _vep_path("spliceai", b, "spliceai_scores.masked.indel.hg38.vcf.gz.tbi")
        ),

        # --- LOFTEE ----------------------------------------------------------
        "loftee_dir":               VEP_ROOT / "loftee",   # plugin scripts are build-agnostic
        "loftee_human_ancestor_fa": _vep_path("loftee", b, "human_ancestor.fa.gz"),
        "loftee_gerp": (
            _vep_path("loftee", b, "GERP_scores.final.sorted.txt.gz")
            if b.upper() == "GRCH37"
            else _vep_path("loftee", b, "gerp_conservation_scores.homo_sapiens.GRCh38.bw")
        ),

        # --- VEP plugins dir (build-agnostic) --------------------------------
        "vep_plugins_dir":          VEP_ROOT / "Plugins",
    }

    return {**DATABASE_PATHS, **build_paths}


# ---------------------------------------------------------------------------
# OPTIONAL_DATABASE_PATHS — pipeline degrades gracefully when absent
# These are all build-independent (reference FASTAs, Orphanet, OMIM, etc.)
# ---------------------------------------------------------------------------

OPTIONAL_DATABASE_PATHS: dict = {

    # Reference FASTAs (WhatsHap phasing)
    "reference_fasta":             REFERENCE_DIR / "GRCh38.fa",
    "reference_fasta_fai":         REFERENCE_DIR / "GRCh38.fa.fai",
    "reference_fasta_grch37":      REFERENCE_DIR / "GRCh37.fa",
    "reference_fasta_fai_grch37":  REFERENCE_DIR / "GRCh37.fa.fai",

    # Orphanet + OMIM (inheritance lookup — hpo_matcher.py)
    "orphanet_genes":              DATABASE_DIR / "orphanet" / "genes_diseases.xml",
    "orphanet_inheritance_tsv":    DATABASE_DIR / "orphanet" / "orphanet_disease_gene_inheritance.tsv",
    "omim_morbidmap":              DATABASE_DIR / "omim_tmp" / "morbidmap.txt",

    # RepeatMasker (Agent 8 — PM4/BP3)
    "repeatmasker":                DATABASE_DIR / "repeatmasker" / "repeatmasker.bed.gz",

    # UniProt (Agent 5 — PM1)
    "uniprot":                     DATABASE_DIR / "uniprot" / "uniprot_human_features.tsv",

    # CADD raw (only if needed outside dbNSFP coverage)
    "cadd_snv":                    DATABASE_DIR / "cadd" / "whole_genome_SNVs.tsv.gz",
}

# ---------------------------------------------------------------------------
# VEP runtime settings  — used by the vep_runner node
# ---------------------------------------------------------------------------

VEP_SETTINGS: dict = {
    "cache_version":  115,
    "species":        "homo_sapiens",
    "assembly":       "GRCh38",
    "cache_dir":      str(VEP_ROOT),        # vep --dir
    "plugins_dir":    str(VEP_ROOT / "Plugins"),

    # Plugins to enable (all data already present)
    "plugins": [
        f"dbNSFP,{VEP_ROOT / 'dbnsfp' / 'dbNSFP5.3.1a_grch38.gz'},"
        "REVEL_score,SIFT_score,Polyphen2_HDIV_score,MutationTaster_score,"
        "MetaSVM_score,GERP++_RS,phyloP100way_vertebrate",

        f"SpliceAI,snv={VEP_ROOT / 'spliceai' / 'spliceai_scores.masked.snv.hg38.vcf.gz'},"
        f"indel={VEP_ROOT / 'spliceai' / 'spliceai_scores.masked.indel.hg38.vcf.gz'}",

        f"LoF,loftee_path:{VEP_ROOT / 'loftee'},"
        f"human_ancestor_fa:{VEP_ROOT / 'loftee' / 'human_ancestor.fa.gz'},"
        f"gerp_bigwig:{VEP_ROOT / 'loftee' / 'gerp_conservation_scores.homo_sapiens.GRCh38.bw'}",
    ],

    # Custom annotations
    "custom": [
        f"{VEP_ROOT / 'clinvar' / 'clinvar.vcf.gz'},"
        "ClinVar,vcf,exact,0,CLNSIG,CLNREVSTAT,CLNDN,CLNACC",
    ],

    # Standard VEP flags for clinical use
    "extra_flags": [
        "--everything",
        "--canonical",
        "--hgvs",
        "--hgvsg",
        "--symbol",
        "--gene_phenotype",
        "--af",
        "--af_gnomad",
        "--max_af",
        "--pubmed",
        "--numbers",       # exon/intron numbering
        "--no_intergenic", # drop intergenic (matches include_intergenic=False default)
    ],
}


# ---------------------------------------------------------------------------
# Convenience helper — check which required databases are missing
# ---------------------------------------------------------------------------

def check_databases(verbose: bool = True, genome_build: str = "GRCh38") -> Dict[str, bool]:
    """
    Check which paths exist on disk for a given genome build.
    Pass genome_build="GRCh37" to check GRCh37 plugin paths instead.
    """
    status: Dict[str, bool] = {}
    all_paths = {**get_database_paths(genome_build), **OPTIONAL_DATABASE_PATHS}

    required_keys = set(DATABASE_PATHS.keys()) | set(get_database_paths(genome_build).keys())

    missing_required = []
    missing_optional = []

    for key, path in all_paths.items():
        exists = Path(path).exists()
        status[key] = exists
        if not exists:
            if key in required_keys:
                missing_required.append((key, path))
            else:
                missing_optional.append((key, path))

    if verbose:
        print(f"\n=== Database check for {genome_build} ===")
        if missing_required:
            print("\n[REQUIRED — MISSING]")
            for key, path in missing_required:
                print(f"  {key}: {path}")
        else:
            print("\n[REQUIRED] All present ✓")

        if missing_optional:
            print("\n[OPTIONAL — not yet downloaded]")
            for key, path in missing_optional:
                print(f"  {key}: {path}")

        print(f"\nRequired : {len(required_keys) - len(missing_required)}/{len(required_keys)} present")
        print(f"Optional : {len(OPTIONAL_DATABASE_PATHS) - len(missing_optional)}/{len(OPTIONAL_DATABASE_PATHS)} present")

    return status

###REPORT####

from src.pipeline.nodes.report_generator import ReportConfig

REPORT_CONFIG = ReportConfig(
    lab_name         = os.getenv("LAB_NAME", "Genomics Laboratory"),
    lab_contact      = os.getenv("LAB_CONTACT", ""),
    logo_path        = os.getenv("LAB_LOGO_PATH"),       # absolute path to PNG/SVG
    pipeline_version = "1.0",
    disclaimer = os.getenv("REPORT_DISCLAIMER", "This report is intended for clinical research use only. Variant classifications should be interpreted by a qualified clinical geneticist in the context of the patient's full clinical presentation."),
)

