"""
LEGACY COMPATIBILITY WRAPPER

This file is kept for backward compatibility only.
All new code should import from src.utils.llm instead.

This wrapper redirects to the unified LLM client which supports
both AWS Bedrock and vLLM based on configuration.
"""

# Import from unified client
from src.utils.llm import call_llm, call_llm_json

# Re-export for backward compatibility
__all__ = ["call_llm", "call_llm_json"]
