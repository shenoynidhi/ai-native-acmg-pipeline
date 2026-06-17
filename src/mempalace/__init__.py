"""
MemPalace - Semantic memory system for ACMG Pipeline

Hierarchical memory structure:
- Wings: High-level categories (analysis_history, preferences, variants)
- Rooms: Specific contexts (gene names, session IDs, preference types)
- Content: Actual memories with embeddings for semantic search

Knowledge Graph:
- Tracks variant/gene relationships over time
- Records how classifications change
- Links sessions to variants
"""

from src.mempalace.palace import (
    mine_memory,
    search_memories,
    wake_up,
    delete_memory,
    update_memory
)

from src.mempalace.knowledge_graph import (
    record_classification,
    get_variant_history,
    get_gene_variants,
    track_reclassification
)

__all__ = [
    # Core memory operations
    'mine_memory',
    'search_memories',
    'wake_up',
    'delete_memory',
    'update_memory',

    # Knowledge graph operations
    'record_classification',
    'get_variant_history',
    'get_gene_variants',
    'track_reclassification',
]

