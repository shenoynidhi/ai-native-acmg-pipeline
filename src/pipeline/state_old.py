"""
src/pipeline/state.py

VariantState: the single data structure that flows through every node and agent
in the LangGraph pipeline. Every node receives the full state and returns a dict
containing only the fields it changed.

Design rules:
  - All fields are Optional where the value isn't known at pipeline entry.
  - Fields are grouped by the phase that populates them.
  - No business logic lives here — pure data container only.
  - Default factories (list, dict) are handled at initialisation time via
    build_initial_state(), NOT as TypedDict defaults (TypedDict doesn't support them).
"""

from typing import TypedDict, List, Dict, Optional, Literal, Any


# ---------------------------------------------------------------------------
# Sub-types
# ---------------------------------------------------------------------------

class AgentEvidence(TypedDict):
    """
    Evidence object returned by each specialist agent (Agents 1–9).
    Stored in VariantState["agent_evidence"]["agent1"] … ["agent9"].
    """
    criteria_pathogenic: Dict[str, str]   # e.g. {"PVS1": "VeryStrong", "PM2": "Moderate"}
    criteria_benign:     Dict[str, str]   # e.g. {"BA1": "StandAlone"}
    evidence_notes:      str              # human-readable explanation for the report
    citations:           List[str]        # e.g. ["PubMed:28369373", "ClinVar:RCV000031349"]
    confidence:          Literal["HIGH", "MEDIUM", "LOW"]


# ---------------------------------------------------------------------------
# Main state
# ---------------------------------------------------------------------------

class VariantState(TypedDict):
    """
    Complete state for one variant flowing through the pipeline.
    Populated progressively: each node adds its fields and passes the rest through.

    Phase map:
      Phase 0  — session / system fields (set before graph entry)
      Phase 1  — variant identifiers (set by post_process_node after VEP)
      Phase 2  — population frequency (set by post_process_node)
      Phase 3  — ClinVar fields (set by post_process_node)
      Phase 4  — in-silico scores (set by post_process_node)
      Phase 5  — structural / consequence fields (set by post_process_node)
      Phase 6  — gene-level fields (set by post_process_node)
      Phase 7  — phasing fields (set by phasing_node)
      Phase 8  — agent outputs (set by run_agents_in_parallel)
      Phase 9  — evidence aggregator outputs (set by evidence_aggregator_node)
      Phase 10 — debate outputs / final classification (set by final_arbiter_node)
      Phase 11 — phenotype / HPO outputs (set by hpo_* and phenotype_scorer nodes)
    """

    # -------------------------------------------------------------------------
    # Phase 0 — session / system  (required at graph entry)
    # -------------------------------------------------------------------------
    session_id:        str
    warnings:          List[str]   # non-fatal issues accumulated across nodes

    # Path to input VCF (set by API / runner before invoking the graph)
    proband_vcf_path:  str

    # Optional case database for PS4 evaluation (set by API / runner)
    case_database_csv: Optional[str]   # Path to user case cohort CSV

    # Trio mode — optional parental VCFs (None = solo mode)
    parent1_vcf_path:  Optional[str]   # typically maternal VCF
    parent2_vcf_path:  Optional[str]   # typically paternal VCF
    trio_mode:         bool            # True if both parental VCFs provided
    proband_sex: Optional[str]   # "male" | "female" | "unknown"
    parent1_genotype:  Optional[str]   # GT at this locus from parent1 VCF e.g. "0/1"
    parent2_genotype:  Optional[str]   # GT at this locus from parent2 VCF e.g. "0/0"
    denovo_status:     Optional[str]   # "confirmed" | "possible" | "excluded" | "unknown"
    genome_build:      str         # "GRCh38" or "GRCh37"
    proband_bam_path:  Optional[str]
    parent1_bam_path:  Optional[str]
    parent2_bam_path:  Optional[str]
    parsed_variants: Optional[list]   # all variants from VEP TSV; set by post_process_node
    annotated_tsv:   Optional[str]    # path to VEP TSV output; set by vep_runner_node
    # -------------------------------------------------------------------------
    # Phase 1 — variant identifiers  (populated by post_process_node)
    # -------------------------------------------------------------------------
    variant_id:        str           # canonical key: "chr1:12345:A:T"
    gene:              str           # HGNC gene symbol, e.g. "BRCA2"
    transcript:        Optional[str] # Ensembl canonical transcript, e.g. "ENST00000544455"
    hgvsc:             Optional[str] # e.g. "NM_007294.4:c.4327C>T"
    hgvsp:             Optional[str] # e.g. "NP_009225.1:p.Arg1443Ter"
    consequence:       str           # VEP most severe: "stop_gained", "missense_variant", etc.
    protein_position:  Optional[int] # amino-acid position (None for non-coding)
    amino_acid_change:        Optional[str]   # e.g. "Arg/Trp" from VEP
    repeat_region:            bool            # True if VEP flags low-complexity/repeat
    gene_clingen_mechanism:   Optional[str]   # e.g. "loss of function" from ClinGen CSV
    gnomad_mis_z:             Optional[float] # gnomAD missense Z-score
    gnomad_oe_mis:            Optional[float] # gnomAD observed/expected missense
    exon_number:       Optional[str] # e.g. "15/23"  (exon 15 of 23 total)
    intron_number:     Optional[str] # e.g. "14/22"

    # -------------------------------------------------------------------------
    # Phase 2 — gnomAD population frequency  (populated by post_process_node)
    # -------------------------------------------------------------------------
    max_gnomad_af:           float              # highest AF across all populations
    gnomad_af_popmax:        float              # gnomAD popmax AF
    gnomad_nhomalt:          int                # homozygous individuals in gnomAD
    gnomad_af_by_population: Dict[str, float]  # {"afr": 0.001, "eas": 0.0, ...}

    # -------------------------------------------------------------------------
    # Phase 3 — ClinVar  (populated by post_process_node)
    # -------------------------------------------------------------------------
    clinvar_clnsig:    Optional[str]  # e.g. "Pathogenic", "Likely_benign"
    clinvar_stars:     int            # 0–4 review status stars
    clinvar_disease:   Optional[str]  # disease name from CLNDN
    clinvar_accession: Optional[str]  # e.g. "RCV000031349"

    # -------------------------------------------------------------------------
    # Phase 4 — in-silico predictor scores  (populated by post_process_node)
    # All sourced from VEP + dbNSFP plugin output; None = score not available
    # -------------------------------------------------------------------------
    is_loftee_hc:           bool            # LOFTEE high-confidence LoF
    max_spliceai:           float           # max of DS_AG, DS_AL, DS_DG, DS_DL
    revel_score:            Optional[float] # 0–1, higher = more pathogenic
    cadd_phred:             Optional[float] # from dbNSFP column CADD_phred
    sift_score:             Optional[float] # 0–1, lower = more damaging
    polyphen2_score:        Optional[float] # 0–1, higher = more damaging
    mutationtaster_score:   Optional[float]
    metasvm_score:          Optional[float]
    eve_score:              Optional[float]
    maxentscan_diff:        Optional[float] # MaxEntScan ref - alt (splice strength change)
    gerp_rs:                Optional[float] # GERP++ RS conservation score
    phylop100way:           Optional[float] # PhyloP 100-way vertebrate

    # Derived vote counts (computed by post_process_node from individual scores)
    insilico_votes_damaging: int   # number of predictors calling damaging
    insilico_votes_benign:   int   # number of predictors calling benign

    # -------------------------------------------------------------------------
    # Phase 5 — structural / consequence flags  (populated by post_process_node)
    # -------------------------------------------------------------------------
    is_inframe_indel: bool   # in-frame insertion or deletion
    is_stop_loss:     bool   # c.*N>X type stop-loss variants

    # -------------------------------------------------------------------------
    # Phase 6 — gene-level context  (populated by post_process_node via lookups)
    # -------------------------------------------------------------------------
    gene_clingen_validity:         Optional[str]   # "Definitive", "Strong", "Moderate", etc.
    gene_orphanet_inheritance:     Optional[str]   # "AD", "AR", "XLR", "XLD", "Mito", etc.
    gene_gnomad_pli:               Optional[float] # gnomAD pLI (0–1; ≥0.9 = constrained)
    gene_gnomad_loeuf:             Optional[float] # gnomAD LOEUF (<0.35 = constrained)
    gene_gnomad_zscore:            Optional[float] # gnomAD missense Z-score
    gene_clinvar_missense_fraction: Optional[float] # fraction of ClinVar P/LP that are missense
    gene_clinvar_lof_fraction:     Optional[float] # fraction of ClinVar P/LP that are LoF

    # -------------------------------------------------------------------------
    # Phase 7 — phasing  (populated by phasing_node using WhatsHap)
    # -------------------------------------------------------------------------
    phase_status:     Optional[str]            # "compound_het_trans" | "compound_het_cis" |
                                     # "unphased" | "not_applicable"
    phase_confidence: Optional[str]            # "HIGH" | "MEDIUM" | "LOW"
    phase_partner:    Optional[str]  # variant_id of the compound-het partner, if any

    # -------------------------------------------------------------------------
    # Phase 8 — agent outputs  (populated by run_agents_in_parallel)
    # -------------------------------------------------------------------------
    agent_evidence: Dict[str, AgentEvidence]
    # Keys: "agent1" … "agent9"
    # Each AgentEvidence holds criteria_pathogenic, criteria_benign,
    # evidence_notes, citations, confidence.

    # -------------------------------------------------------------------------
    # Phase 9 — evidence aggregator  (populated by evidence_aggregator_node)
    # -------------------------------------------------------------------------
    preliminary_classification:      Optional[str]  # before debate
    preliminary_criteria_pathogenic: List[str]      # e.g. ["PVS1", "PM2"]
    preliminary_criteria_benign:     List[str]      # e.g. ["BS1"]
    conflict_flag:                   bool           # True if P and B criteria both present
    ba1_shortcircuit:                bool           # True if BA1 fired → skip debate
    # Evidence aggregator output
    all_criteria_pathogenic:    dict            # merged P criteria across all agents
    all_criteria_benign:        dict            # merged B criteria across all agents
    classification_rules_met:   list            # ACMG Table 5 rule IDs that fired
    aggregator_notes:           Optional[str]  # human-readable evidence count summary

    # Aggregator outputs (new fields)
    pathogenic_counts:               Optional[dict]
    benign_counts:                   Optional[dict]
    unevaluated_criteria:            Optional[list]

    # -------------------------------------------------------------------------
    # Phase 10 — final classification  (populated by final_arbiter_node)
    # -------------------------------------------------------------------------

    # Debate layer outputs
    pathogenic_advocate_result:      Optional[dict]
    benign_advocate_result:          Optional[dict]
    final_classification:            Optional[str]
    evidence_summary:                Optional[str]
    confidence:                      Optional[str]
    recommended_followup:            Optional[str]
    debate_notes:                    Optional[str]
    unevaluated_criteria_report:     Optional[list]
    final_criteria_applied:    List[str]       # definitive list after debate
    reclassification_conditions: Optional[str] # what new evidence would change the call
    all_citations:             List[str]       # merged citations from all agents

    #Additions
    tavtigian_points:           Optional[int]
    tavtigian_classification:   Optional[str]

    # -------------------------------------------------------------------------
    # Phase 11 — phenotype / HPO  (populated by hpo_* and phenotype_scorer nodes)
    # -------------------------------------------------------------------------
    # Patient HPO terms — shared across all variants in a session.
    # Set once by hpo_nlp_node (from clinical notes) or supplied directly.
    clinical_notes: Optional[str]   # raw free-text clinical notes; consumed by hpo_nlp_node
    patient_hpo_terms: List[Dict]
    # Each entry: {"hpo_id": "HP:0001250", "label": "Seizure", "present": True}

    phenotype_score: Optional[float]  # 0.0–1.0 match to patient HPO terms
    hpo_matched_genes: list                # genes matching patient HPO
    gene_orphanet_diseases: list                # Orphanet disease names for gene
    alternate_molecular_diagnosis: Optional[str]       # another causative variant found
    matched_orphanet_disease: Optional[str]   # best-matching Orphanet disease name
    orphanet_id: Optional[str]    # e.g. "ORPHA:199"
    zygosity_filter_status: Optional[str]    # "RETAIN" | "DEPRIORITIZE" | "RETAIN_UNCONFIRMED"
    phenotype_score_notes: Optional[str]   # human-readable explanation of score components
    # -------------------------------------------------------------------------
    # Internal routing flags  (set by detector / filter nodes, read by graph edges)
    # -------------------------------------------------------------------------
    validation_passed:      bool
    vep_already_annotated:  bool  # True = input VCF already has VEP CSQ fields → skip VEP


# ---------------------------------------------------------------------------
# Factory — build a blank state for a new variant
# ---------------------------------------------------------------------------

def build_initial_state(
    session_id:        str,
    proband_vcf_path:  str,
    genome_build:      str = "GRCh38",
    patient_hpo_terms: Optional[List[Dict]] = None,
    parent1_vcf_path:  Optional[str] = None,
    parent2_vcf_path:  Optional[str] = None,
    proband_sex:       Optional[str] = None,
    clinical_notes:    Optional[str] = None,
    proband_bam_path:  Optional[str] = None,
    parent1_bam_path:  Optional[str] = None,
    parent2_bam_path:  Optional[str] = None,
    case_database_csv: Optional[str] = None,
) -> VariantState:
    """
    Return a VariantState pre-filled with safe defaults.
    Call this once per variant before invoking the compiled graph.

    Example:
        state = build_initial_state(
            session_id="abc12345",
            proband_vcf_path="/workspace/data/acmg-pipeline/data/output/abc12345/proband.vcf.gz",
        )
        result = VARIANT_GRAPH.invoke(state)
    """
    trio_mode = (parent1_vcf_path is not None and parent2_vcf_path is not None)
    return VariantState(
        # --- session ---
        session_id        = session_id,
        warnings          = [],
        proband_vcf_path  = proband_vcf_path,
        case_database_csv = case_database_csv,
        parent1_vcf_path  = parent1_vcf_path,
        parent2_vcf_path  = parent2_vcf_path,
        trio_mode         = trio_mode,
        parent1_genotype  = None,
        parent2_genotype  = None,
        proband_sex = proband_sex or "unknown",
        denovo_status     = None,
        genome_build      = genome_build,
        proband_bam_path    = proband_bam_path,
        parent1_bam_path    = parent1_bam_path,
        parent2_bam_path    = parent2_bam_path,
        parsed_variants = None,
        annotated_tsv   = None,

        # --- variant identifiers (filled by post_process_node) ---
        variant_id        = "",
        gene              = "",
        transcript        = None,
        hgvsc             = None,
        hgvsp             = None,
        consequence       = "",
        protein_position  = None,
        amino_acid_change       = None,
        repeat_region           = False,
        gene_clingen_mechanism  = None,
        gnomad_mis_z            = None,
        gnomad_oe_mis           = None,
        exon_number       = None,
        intron_number     = None,

        # --- population frequency ---
        max_gnomad_af            = 0.0,
        gnomad_af_popmax         = 0.0,
        gnomad_nhomalt           = 0,
        gnomad_af_by_population  = {},

        # --- ClinVar ---
        clinvar_clnsig    = None,
        clinvar_stars     = 0,
        clinvar_disease   = None,
        clinvar_accession = None,

        # --- in-silico scores ---
        is_loftee_hc          = False,
        max_spliceai          = 0.0,
        revel_score           = None,
        cadd_phred            = None,
        sift_score            = None,
        polyphen2_score       = None,
        mutationtaster_score  = None,
        metasvm_score         = None,
        eve_score             = None,
        maxentscan_diff       = None,
        gerp_rs               = None,
        phylop100way          = None,
        insilico_votes_damaging = 0,
        insilico_votes_benign   = 0,

        # --- structural flags ---
        is_inframe_indel  = False,
        is_stop_loss      = False,

        # --- gene-level context ---
        gene_clingen_validity          = None,
        gene_orphanet_inheritance      = None,
        gene_gnomad_pli                = None,
        gene_gnomad_loeuf              = None,
        gene_gnomad_zscore             = None,
        gene_clinvar_missense_fraction = None,
        gene_clinvar_lof_fraction      = None,

        # --- phasing ---
        phase_status     = "not_applicable",
        phase_confidence = "LOW",
        phase_partner    = None,

        # --- agents ---
        agent_evidence   = {},

        # --- evidence aggregator ---
        preliminary_classification      = None,
        preliminary_criteria_pathogenic = [],
        preliminary_criteria_benign     = [],
        conflict_flag                   = False,
        ba1_shortcircuit                = False,
        all_criteria_pathogenic    = {},
        all_criteria_benign        = {},
        classification_rules_met   = [],
        aggregator_notes           = None,


        # Aggregator outputs
        pathogenic_counts =               None,
        benign_counts =                   None,
        unevaluated_criteria =            None,

        # Debate layer outputs
        pathogenic_advocate_result =          None,
        benign_advocate_result =              None,
        final_classification =                None,
        final_criteria_applied =              [],
        evidence_summary =                    None,
        confidence =                          None,
        recommended_followup =                None,
        reclassification_conditions =         None,
        debate_notes =                        None,
        unevaluated_criteria_report =         None,
        all_citations =                       [],
        tavtigian_points          = None,
        tavtigian_classification  = None,

        # --- phenotype ---
        clinical_notes = clinical_notes,
        patient_hpo_terms        = patient_hpo_terms or [],
        phenotype_score          = None,
        matched_orphanet_disease = None,
        orphanet_id              = None,
        zygosity_filter_status   = None,
        hpo_matched_genes        = [],
        gene_orphanet_diseases   = [],
        alternate_molecular_diagnosis = None,
        phenotype_score_notes = None,
        # --- routing flags ---
        validation_passed     = False,
        vep_already_annotated = False,
    )

