"""
Central configuration for the ACMG pipeline.
All paths, thresholds, and settings live here.

Adapted for:
  - vLLM backend (Qwen2.5-14B on pod-b) instead of Ollama
  - Existing VEP databases under /workspace/data/.vep/
  - Functional LLM client style (call_llm / call_llm_json)
"""

import os
from pathlib import Path
from typing import Dict
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv("/workspace/data/acmg-pipeline/.env")

# ---------------------------------------------------------------------------
# Base directories
# ---------------------------------------------------------------------------
BASE_DIR     = Path("/workspace/data/acmg-pipeline")
SRC_DIR      = BASE_DIR / "src"
DATA_DIR     = BASE_DIR / "data"
DATABASE_DIR = DATA_DIR / "databases"      # small reference files downloaded here
CHROMADB_DIR = DATA_DIR / "chromadb"
OUTPUT_DIR   = DATA_DIR / "output"
REFERENCE_DIR = DATA_DIR / "reference"

# VEP plugin/cache root (already populated in the pod)
VEP_ROOT     = Path("/workspace/data/.vep")

# ---------------------------------------------------------------------------
# vLLM / LLM settings  (read from .env, fall back to sensible defaults)
# ---------------------------------------------------------------------------
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "http://172.29.127.170:8000/v1")
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

DATABASE_PATHS: dict = {

    # --- VEP cache (homo_sapiens 115 GRCh38, already downloaded) ------------
    "vep_cache":          VEP_ROOT / "homo_sapiens" / "115_GRCh38",

    # --- ClinVar (already in .vep/clinvar/) ----------------------------------
    "clinvar_vcf":        VEP_ROOT / "clinvar" / "clinvar.vcf.gz",
    "clinvar_vcf_tbi":    VEP_ROOT / "clinvar" / "clinvar.vcf.gz.tbi",

    # --- dbNSFP 5.3.1a (already in .vep/dbnsfp/) ----------------------------
    # NOTE: README referenced 4.4a; we have 5.3.1a — path corrected here
    "dbnsfp":             VEP_ROOT / "dbnsfp" / "dbNSFP5.3.1a_grch38.gz",
    "dbnsfp_tbi":         VEP_ROOT / "dbnsfp" / "dbNSFP5.3.1a_grch38.gz.tbi",

    # --- gnomAD (already in .vep/gnomad/) ------------------------------------
    # tabbed TSV format (not the full VCF — used for allele frequency lookup)
    "gnomad_tabbed":      VEP_ROOT / "gnomad" / "gnomad.ch.genomesv3.tabbed.tsv.gz",
    "gnomad_tabbed_tbi":  VEP_ROOT / "gnomad" / "gnomad.ch.genomesv3.tabbed.tsv.gz.tbi",
    # Constraint metrics (pLI, LOEUF) — need to download separately (small file)
    "gnomad_constraint":  DATABASE_DIR / "gnomad" / "gnomad.v2.1.1.lof_metrics.by_gene.txt",

    # --- SpliceAI (already in .vep/spliceai/) --------------------------------
    "spliceai_snv":       VEP_ROOT / "spliceai" / "spliceai_scores.masked.snv.hg38.vcf.gz",
    "spliceai_snv_tbi":   VEP_ROOT / "spliceai" / "spliceai_scores.masked.snv.hg38.vcf.gz.tbi",
    "spliceai_indel":     VEP_ROOT / "spliceai" / "spliceai_scores.masked.indel.hg38.vcf.gz",
    "spliceai_indel_tbi": VEP_ROOT / "spliceai" / "spliceai_scores.masked.indel.hg38.vcf.gz.tbi",

    # --- LOFTEE (already in .vep/loftee/) ------------------------------------
    "loftee_dir":                VEP_ROOT / "loftee",
    "loftee_human_ancestor_fa":  VEP_ROOT / "loftee" / "human_ancestor.fa.gz",
    "loftee_gerp_scores":        VEP_ROOT / "loftee" / "gerp_conservation_scores.homo_sapiens.GRCh38.bw",

    # --- VEP Plugins dir -----------------------------------------------------
    "vep_plugins_dir":    VEP_ROOT / "Plugins",

    # --- HPO (downloaded) ----------------------------------------------------
    "hpo_obo":            DATABASE_DIR / "hpo" / "hp.obo",
    "hpo_annotations":    DATABASE_DIR / "hpo" / "phenotype.hpoa",

    # --- ClinGen (downloaded) ------------------------------------------------
    "clingen_validity":   DATABASE_DIR / "clingen" / "gene_disease_validity.csv",

    # --- HGNC (downloaded) ---------------------------------------------------
    "hgnc":               DATABASE_DIR / "hgnc" / "hgnc_complete_set.txt",
}

# Optional databases — pipeline degrades gracefully when absent.
# Each entry notes which agent/phase needs it and how to obtain it.
OPTIONAL_DATABASE_PATHS: dict = {

    # Needed for WhatsHap phasing (phasing node, Phase 3)
    # wget https://ftp.ncbi.nlm.nih.gov/genomes/refseq/vertebrate_mammalian/Homo_sapiens/
    #   all_assembly_versions/GCF_000001405.40_GRCh38.p14/
    #   GCF_000001405.40_GRCh38.p14_genomic.fna.gz -O data/reference/GRCh38.fa.gz
    # then: gunzip data/reference/GRCh38.fa.gz && samtools faidx data/reference/GRCh38.fa
    "reference_fasta":     REFERENCE_DIR / "GRCh38.fa",
    "reference_fasta_fai": REFERENCE_DIR / "GRCh38.fa.fai",

    # Needed for Agent 9 (PP4/BP5 — phenotype/disease matching)
    # Manual download from https://www.orphadata.com/genes/
    # Files: en_product6.xml → genes_diseases.xml, en_product9_ages.xml → epidemiology.xml
    "orphanet_genes":        DATABASE_DIR / "orphanet" / "genes_diseases.xml",
    "orphanet_epidemiology": DATABASE_DIR / "orphanet" / "epidemiology.xml",

    # Needed for Agent 8 (PM4/BP3 — in-repeat indel evidence)
    # wget "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database/rmsk.txt.gz"
    # then convert to sorted BED: zcat rmsk.txt.gz | awk '{print $6"\t"$7"\t"$8"\t"$13}' \
    #   | sort -k1,1 -k2,2n | bgzip > repeatmasker.bed.gz && tabix -p bed repeatmasker.bed.gz
    "repeatmasker":       DATABASE_DIR / "repeatmasker" / "repeatmasker.bed.gz",

    # Needed for Agent 5 (PM1 — critical functional domain evidence)
    # Download UniProt human reviewed features TSV from:
    # https://www.uniprot.org/uniprot/?query=organism:9606+reviewed:yes&format=tsv
    # Include columns: Gene names, Features (Active site, Binding site, Domain, etc.)
    "uniprot":            DATABASE_DIR / "uniprot" / "uniprot_human_features.tsv",

    # CADD raw scores — NOT needed separately.
    # CADD_phred is available as a column in dbNSFP 5.3.1a (already present).
    # Only add this if you need CADD for variants outside dbNSFP coverage.
    "cadd_snv":           DATABASE_DIR / "cadd" / "whole_genome_SNVs.tsv.gz",
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

# ---------------------------------------------------------------------------
# VEP runtime binaries
# Use the vep conda env's own Perl to avoid @INC module-not-found errors.
# The system Perl at /usr/bin/perl does NOT have the conda Ensembl modules.
# ---------------------------------------------------------------------------
VEP_BINARY = Path("/workspace/data/envs/vep/share/ensembl-vep-115.2-1/vep")
VEP_PERL   = Path("/workspace/data/envs/vep/bin/perl")
BCFTOOLS_BINARY = Path("/workspace/data/envs/bcftools_env/bin/bcftools")
SAMTOOLS_BINARY = Path("/workspace/data/envs/bcftools_env/bin/samtools")

def check_databases(verbose: bool = True) -> Dict[str, bool]:
    """
    Check which DATABASE_PATHS (required) and OPTIONAL_DATABASE_PATHS entries
    exist on disk. Prints a summary and returns combined {key: True/False} dict.
    """
    status: Dict[str, bool] = {}

    required_missing = []
    for key, path in DATABASE_PATHS.items():
        exists = Path(path).exists()
        status[key] = exists
        if not exists:
            required_missing.append((key, path))

    optional_missing = []
    for key, path in OPTIONAL_DATABASE_PATHS.items():
        exists = Path(path).exists()
        status[key] = exists
        if not exists:
            optional_missing.append((key, path))

    if verbose:
        if required_missing:
            print("\n[REQUIRED — MISSING]")
            for key, path in required_missing:
                print(f"  {key}: {path}")
        else:
            print("\n[REQUIRED] All present ✓")

        if optional_missing:
            print("\n[OPTIONAL — not yet downloaded]")
            for key, path in optional_missing:
                print(f"  {key}: {path}")

        req_total  = len(DATABASE_PATHS)
        req_ok     = req_total - len(required_missing)
        opt_total  = len(OPTIONAL_DATABASE_PATHS)
        opt_ok     = opt_total - len(optional_missing)
        print(f"\nRequired : {req_ok}/{req_total} present")
        print(f"Optional : {opt_ok}/{opt_total} present")

    return status


if __name__ == "__main__":
    print("=== Database path check ===")
    results = check_databases(verbose=True)
    missing = [k for k, v in results.items() if not v]
    present = [k for k, v in results.items() if v]
    print(f"\nPresent : {len(present)}/{len(results)}")
    print(f"Missing : {len(missing)}/{len(results)}")
    if missing:
        print("\nMissing keys:", missing)
