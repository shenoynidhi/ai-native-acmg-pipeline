"""
src/pipeline/progress_emitter.py

Helper to emit detailed progress events throughout the pipeline.
Wraps the ProgressCallback with convenient methods for each pipeline stage.
"""

from typing import Optional, Any
from src.utils.logging_config import ProgressCallback


class DetailedProgressEmitter:
    """
    Wrapper around ProgressCallback that provides convenient methods
    for emitting progress at each pipeline stage.
    """

    def __init__(self, callback: Optional[ProgressCallback], total_variants: int, current_variant: int = 0):
        self.callback = callback
        self.total_variants = total_variants
        self.current_variant = current_variant
        self.base_progress = 0.25  # VEP takes 25%
        self.variant_progress_range = 0.70  # Variants take 70%

    def _emit(self, stage: str, sub_progress: float, message: str, **kwargs):
        """Internal: emit if callback exists."""
        if self.callback:
            # Calculate overall progress:
            # VEP: 0-25%
            # Each variant contributes: 25% + (70% / total) * (variant_num + sub_progress)
            if self.current_variant > 0:
                variant_contribution = (self.variant_progress_range / self.total_variants)
                variant_base = self.base_progress + variant_contribution * (self.current_variant - 1)
                overall_progress = variant_base + (variant_contribution * sub_progress)
            else:
                # Pre-VEP stages
                overall_progress = sub_progress

            self.callback.update(stage, overall_progress, message, **kwargs)

    # VEP and preprocessing
    def vep_starting(self):
        self._emit('vep_annotation', 0.05, 'Starting VEP annotation')

    def vep_running(self):
        self._emit('vep_annotation', 0.15, 'VEP annotation in progress')

    def vep_complete(self, variant_count: int):
        self._emit('vep_annotation', 0.25, f'VEP complete - {variant_count} variants parsed', variant_count=variant_count)

    def filtering_variants(self):
        self._emit('filtering', 0.23, 'Filtering variants')

    # Per-variant stages
    def variant_starting(self, variant_id: str, gene: str, number: int):
        self.current_variant = number
        self._emit('evidence_collection', 0.0, f'Starting analysis of {gene} variant ({number}/{self.total_variants})',
                   variant_id=variant_id, gene=gene, variant_number=number)

    def agent_running(self, agent_name: str, variant_id: str, gene: str):
        # Agents 1-9 + debate = ~12 sub-steps, each agent is ~8% of variant processing
        agent_map = {
            'Population Frequency': 0.08,
            'Consequence Analysis': 0.16,
            'In Silico Prediction': 0.24,
            'ClinVar Evidence': 0.32,
            'Functional Evidence': 0.40,
            'Segregation Analysis': 0.48,
            'De Novo Analysis': 0.56,
            'Gene-Disease Context': 0.64,
            'Phenotype Match': 0.72,
            'Evidence Aggregator': 0.78,
            'Pathogenic Advocate': 0.84,
            'Benign Advocate': 0.90,
            'Final Arbiter': 0.96,
        }
        sub_progress = agent_map.get(agent_name, 0.5)
        self._emit('evidence_collection', sub_progress,
                   f'{agent_name}: {gene} ({self.current_variant}/{self.total_variants})',
                   variant_id=variant_id, gene=gene, agent=agent_name)

    def variant_complete(self, variant_id: str, gene: str, classification: str):
        self._emit('evidence_collection', 1.0,
                   f'Completed {gene}: {classification} ({self.current_variant}/{self.total_variants})',
                   variant_id=variant_id, gene=gene, classification=classification)

    # Final stages
    def generating_reports(self):
        self._emit('report_generation', 0.95, 'Generating TSV, XLSX, and HTML reports')

    def saving_memory(self):
        self._emit('report_generation', 0.98, 'Saving to MemPalace')

    def complete(self, variant_count: int):
        self._emit('complete', 1.0, f'Classification complete - {variant_count} variants', variant_count=variant_count)

