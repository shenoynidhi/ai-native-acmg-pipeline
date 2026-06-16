"""
src/utils/logging_config.py

Centralized logging configuration for ACMG pipeline.
Suppresses cosmetic warnings while preserving actionable clinical information.
"""

import logging
import warnings
import sys
import os


def configure_pipeline_logging(level=logging.INFO, suppress_warnings=True):
    """
    Configure logging for the ACMG pipeline.

    Suppresses cosmetic noise:
    - ChromaDB telemetry errors (version mismatch)
    - LangChain deprecation warnings
    - cyvcf2 VCF contig warnings (test files)

    Preserves actionable warnings:
    - Zygosity filter messages
    - Disease matching results
    - Phase status warnings

    Args:
        level: Root logging level (default: INFO)
        suppress_warnings: If True, suppress cosmetic warnings (default: True)
    """
    # Root logger configuration
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    if suppress_warnings:
        # Suppress ChromaDB telemetry errors (version 0.4.24 has broken PostHog integration)
        # These errors are cosmetic - telemetry is already disabled via CHROMA_TELEMETRY=0
        logging.getLogger('chromadb').setLevel(logging.ERROR)
        logging.getLogger('chromadb.telemetry').setLevel(logging.CRITICAL)

        # Suppress LangChain deprecation warnings (future API changes, no immediate impact)
        warnings.filterwarnings('ignore', category=DeprecationWarning, module='langgraph')
        warnings.filterwarnings('ignore', message='.*allowed_objects.*')

        # Suppress cyvcf2 UserWarnings (VCF contig header issues on test files)
        # Production VCFs with proper headers won't trigger these
        warnings.filterwarnings('ignore', message='no intervals found')
        warnings.filterwarnings('ignore', message='The index file is older')

        # Suppress sentence-transformers model loading verbosity
        logging.getLogger('sentence_transformers').setLevel(logging.WARNING)

        # Suppress chromadb_client version detection logs (technical noise)
        logging.getLogger('chromadb_client').setLevel(logging.WARNING)

        # Suppress htslib stderr warnings (C library, shown via cyvcf2)
        # These appear as "[W::hts_idx_load3]" and "[W::vcf_parse]" in stderr
        # Can't be fully suppressed without redirecting stderr globally
        os.environ['HTS_LOG_LEVEL'] = 'error'  # Only show errors, not warnings


def get_user_friendly_logger(agent_name: str) -> logging.Logger:
    """
    Get a logger with user-friendly agent name for clinical users.

    Maps internal agent names (agent4_database) to clinical names (ClinVar Evidence).

    Args:
        agent_name: Internal agent name (e.g., 'agent4_database')

    Returns:
        Logger with user-friendly name

    Example:
        >>> logger = get_user_friendly_logger('agent4_database')
        >>> logger.info('Evaluating variant')
        2026-06-17 10:15:00 INFO [ClinVar Evidence] Evaluating variant
    """
    friendly_names = {
        # Evidence collection agents
        'agent1_population': 'Population Frequency',
        'agent2_consequence': 'Consequence Analysis',
        'agent3_insilico': 'In Silico Prediction',
        'agent4_database': 'ClinVar Evidence',
        'agent5_functional': 'Functional Evidence',
        'agent6_segregation': 'Segregation Analysis',
        'agent7_denovo': 'De Novo Analysis',
        'agent8_gene_context': 'Gene-Disease Context',
        'agent9_phenotype': 'Phenotype Match',

        # Debate phase
        'pathogenic_advocate': 'Pathogenic Analysis',
        'benign_advocate': 'Benign Analysis',
        'final_arbiter': 'Final Classification',

        # Pipeline nodes
        'evidence_aggregator': 'Evidence Summary',
        'hpo_matcher': 'Phenotype Matching',
        'report_generator': 'Report Generation',
        'vep_runner': 'VEP Annotation',
        'post_process': 'Variant Processing',
        'phenotype_scorer': 'Phenotype Scoring',
        'zygosity_filter': 'Zygosity Filter',
    }

    display_name = friendly_names.get(agent_name, agent_name)
    return logging.getLogger(display_name)


class ProgressCallback:
    """
    Progress callback interface for API integration (future use).

    Allows the pipeline to emit structured progress events that can be:
    - Displayed as progress bars in web UI
    - Streamed via WebSocket/SSE
    - Logged to external monitoring systems

    Example:
        >>> def on_progress(event):
        ...     print(f"{event['stage']}: {event['progress']*100:.0f}%")
        >>>
        >>> callback = ProgressCallback(on_progress)
        >>> callback.update('vep_annotation', 0.25, 'VEP complete')
        vep_annotation: 25%
    """

    def __init__(self, callback_fn=None):
        """
        Initialize progress callback.

        Args:
            callback_fn: Optional function(event: dict) called on progress updates
        """
        self.callback_fn = callback_fn

    def update(self, stage: str, progress: float, message: str, **kwargs):
        """
        Emit a progress event.

        Args:
            stage: Pipeline stage (e.g., 'vep_annotation', 'evidence_collection')
            progress: Float 0.0-1.0 indicating completion percentage
            message: Human-readable status message
            **kwargs: Additional metadata (variant_id, gene, etc.)
        """
        if self.callback_fn:
            event = {
                'stage': stage,
                'progress': progress,
                'message': message,
                **kwargs
            }
            self.callback_fn(event)


# Convenience function for formatting session headers
def log_session_header(session_id: str, genome_build: str, variant_count: int = None):
    """
    Log a formatted session header for clean output.

    Args:
        session_id: Session identifier
        genome_build: Genome build (GRCh37 or GRCh38)
        variant_count: Optional variant count
    """
    logger = logging.getLogger('pipeline')
    logger.info("=" * 70)
    logger.info(f"ACMG Classification Pipeline")
    logger.info(f"Session:      {session_id}")
    logger.info(f"Genome Build: {genome_build}")
    if variant_count:
        logger.info(f"Variants:     {variant_count}")
    logger.info("=" * 70)


def log_session_footer(variant_count: int, report_paths: dict):
    """
    Log a formatted session footer for clean output.

    Args:
        variant_count: Number of variants processed
        report_paths: Dict of {format: path}
    """
    logger = logging.getLogger('pipeline')
    logger.info("=" * 70)
    logger.info(f"✓ Classification complete: {variant_count} variant(s) processed")
    logger.info(f"  Reports generated:")
    for fmt, path in report_paths.items():
        logger.info(f"    {fmt.upper()}: {path}")
    logger.info("=" * 70)

