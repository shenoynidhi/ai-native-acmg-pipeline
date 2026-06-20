"""
src/pipeline/run_context.py

PipelineRunContext: session-level data shared across ALL variants in one run.
This is separate from VariantState (which is per-variant).

Analogy:
  - PipelineRunContext  = the patient's folder (one per analysis job)
  - VariantState        = one variant's worksheet inside that folder

The context is created once by the API / CLI entry point and passed into
every node that needs session-level information (file paths, config, HPO terms).
It is NOT part of the LangGraph state — it is passed as a closure or argument
to node functions that need it.

# NOTE: Not yet instantiated by runner.py — will be used by src/api/worker.py (Step 6)
# runner.py currently carries session-level data directly in VariantState.
# Do not refactor until the API layer is built.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict

from src.config import PipelineConfig, OUTPUT_DIR


@dataclass
class PipelineRunContext:
    """
    All session-level state for one pipeline run.

    Usage:
        ctx = PipelineRunContext(
            proband_vcf=Path("/data/patient1.vcf.gz"),
            clinical_notes="10-year-old with seizures and hypotonia",
        )
        ctx.setup_work_dir()   # creates output/abc12345/
        print(ctx.session_id)  # "abc12345"
    """

    # -------------------------------------------------------------------------
    # Identity
    # -------------------------------------------------------------------------
    # Auto-generated 8-char hex ID — unique per run, used for all output paths
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # -------------------------------------------------------------------------
    # User-supplied inputs
    # -------------------------------------------------------------------------
    proband_vcf:    Optional[Path] = None   # required for solo and trio modes
    father_vcf:     Optional[Path] = None   # trio mode only
    mother_vcf:     Optional[Path] = None   # trio mode only
    proband_bam:    Optional[Path] = None   # optional; used by WhatsHap for phasing

    # Clinical free-text notes OR pre-extracted HPO term IDs (one or both)
    clinical_notes: Optional[str]       = None  # e.g. "Seizures, hypotonia, global delay"
    hpo_terms:      Optional[List[str]] = None  # e.g. ["HP:0001250", "HP:0001290"]

    # -------------------------------------------------------------------------
    # Analysis parameters
    # -------------------------------------------------------------------------
    config:        PipelineConfig = field(default_factory=PipelineConfig)
    analysis_mode: str = "solo"      # "solo" | "trio"
    proband_sex:   str = "unknown"   # "male" | "female" | "unknown"
    genome_build:  str = "GRCh38"   # "GRCh38" | "GRCh37"

    # -------------------------------------------------------------------------
    # Intermediate file paths (populated as pipeline progresses)
    # These are written by nodes and read by downstream nodes.
    # -------------------------------------------------------------------------
    filtered_vcf:   Optional[Path] = None   # after prefilter_node
    phased_vcf:     Optional[Path] = None   # after phasing_node
    annotated_vcf:  Optional[Path] = None   # after vep_runner_node
    annotated_tsv:  Optional[Path] = None   # after post_process_node

    # -------------------------------------------------------------------------
    # Phenotype data (populated by hpo_nlp_node or supplied directly)
    # -------------------------------------------------------------------------
    # Full HPO term objects with metadata — richer than the raw hpo_terms list
    patient_hpo_terms: List[Dict] = field(default_factory=list)
    # e.g. [{"hpo_id": "HP:0001250", "label": "Seizure", "present": True}, ...]

    # Ranked gene list from HPO matching — used to boost phenotype-relevant variants
    priority_genes: List[Dict] = field(default_factory=list)
    # e.g. [{"gene": "SCN1A", "score": 0.92, "disease": "Dravet syndrome"}, ...]

    # -------------------------------------------------------------------------
    # Working directory (derived, not stored — always computed from session_id)
    # -------------------------------------------------------------------------
    @property
    def work_dir(self) -> Path:
        """
        Session output directory: data/output/<session_id>/
        All intermediate and final files for this run live here.
        """
        return OUTPUT_DIR / self.session_id

    def setup_work_dir(self) -> Path:
        """
        Create the session working directory and standard subdirectories.
        Call this once at the start of a run before any nodes execute.
        Returns the work_dir Path.
        """
        dirs = [
            self.work_dir,
            self.work_dir / "vep_out",     # VEP raw output files
            self.work_dir / "reports",     # final Excel / HTML / TSV reports
            self.work_dir / "intermediates",  # filtered/phased VCFs
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        return self.work_dir

    # -------------------------------------------------------------------------
    # Validation helpers
    # -------------------------------------------------------------------------
    def validate(self) -> List[str]:
        """
        Check that required inputs are present and files exist on disk.
        Returns a list of error strings (empty = all OK).
        Call before setup_work_dir() to fail fast with clear messages.
        """
        errors: List[str] = []

        if self.proband_vcf is None:
            errors.append("proband_vcf is required but was not provided.")
        elif not Path(self.proband_vcf).exists():
            errors.append(f"proband_vcf not found on disk: {self.proband_vcf}")

        if self.analysis_mode == "trio":
            if self.father_vcf is None and self.mother_vcf is None:
                errors.append("trio mode requires at least one parent VCF.")
            if self.father_vcf and not Path(self.father_vcf).exists():
                errors.append(f"father_vcf not found: {self.father_vcf}")
            if self.mother_vcf and not Path(self.mother_vcf).exists():
                errors.append(f"mother_vcf not found: {self.mother_vcf}")

        if self.proband_bam and not Path(self.proband_bam).exists():
            errors.append(f"proband_bam not found: {self.proband_bam}")

        if self.analysis_mode not in ("solo", "trio"):
            errors.append(f"analysis_mode must be 'solo' or 'trio', got: {self.analysis_mode}")

        if self.genome_build not in ("GRCh38", "GRCh37"):
            errors.append(f"genome_build must be GRCh38 or GRCh37, got: {self.genome_build}")

        if self.clinical_notes is None and self.hpo_terms is None:
            # Not a hard error — pipeline will skip HPO matching and phenotype scoring
            pass  # handled gracefully downstream via conditional edges

        return errors

    def summary(self) -> str:
        """One-line summary for logging at run start."""
        vcf_name = Path(self.proband_vcf).name if self.proband_vcf else "None"
        hpo_count = len(self.hpo_terms) if self.hpo_terms else 0
        return (
            f"[{self.session_id}] mode={self.analysis_mode} "
            f"build={self.genome_build} vcf={vcf_name} "
            f"hpo_terms={hpo_count}"
        )
