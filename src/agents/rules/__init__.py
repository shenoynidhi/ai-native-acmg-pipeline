"""
src/agents/rules/__init__.py

Rule-based (deterministic) agent implementations.
These replace LLM calls with fast, accurate algorithmic logic.
"""

from .deterministic_agents import (
    agent1_population_rules,
    agent3_insilico_rules,
    agent7_denovo_rules,
)

__all__ = [
    'agent1_population_rules',
    'agent3_insilico_rules',
    'agent7_denovo_rules',
]

