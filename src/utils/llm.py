"""
Unified LLM client for ACMG pipeline.

Automatically routes to AWS Bedrock (default) or legacy vLLM based on config.
All agents should import from this module for LLM calls.

Usage:
    from src.utils.llm import call_llm, call_llm_json

    response = call_llm(
        system_prompt="You are an ACMG variant classification expert.",
        user_prompt="Evaluate PM2 for chr13:32338080:A:C",
        temperature=0.1,
        max_tokens=1000
    )
"""

import logging
from typing import Dict
from src.config import LLM_PROVIDER

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy imports based on provider
# ---------------------------------------------------------------------------

def _get_client():
    """Get appropriate LLM client based on config."""
    provider = LLM_PROVIDER.lower()

    if provider == "bedrock":
        try:
            from src.utils.bedrock_client import call_llm, call_llm_json
            logger.info("Using AWS Bedrock for LLM calls")
            return call_llm, call_llm_json
        except ImportError as e:
            logger.error(f"Failed to import Bedrock client: {e}")
            logger.warning("Falling back to legacy vLLM client")
            provider = "vllm"

    if provider == "vllm":
        try:
            from src.utils.llm_client import call_llm, call_llm_json
            logger.info("Using legacy vLLM for LLM calls")
            return call_llm, call_llm_json
        except ImportError as e:
            logger.error(f"Failed to import vLLM client: {e}")
            raise RuntimeError("No LLM client available") from e

    raise ValueError(f"Unknown LLM provider: {provider}")


# Initialize client functions
_call_llm_fn, _call_llm_json_fn = _get_client()


# ---------------------------------------------------------------------------
# Public API (drop-in replacement for llm_client.py)
# ---------------------------------------------------------------------------

def call_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.1,
    retries: int = 3,
) -> str:
    """
    Call LLM with system + user prompts.

    Args:
        system_prompt: System message defining LLM behavior
        user_prompt: User message with task/question
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature (0.0 = deterministic)
        retries: Number of retry attempts on failure

    Returns:
        Generated text response

    Example:
        response = call_llm(
            system_prompt="You are a helpful assistant.",
            user_prompt="What is PM2 in ACMG guidelines?",
            temperature=0.1
        )
    """
    return _call_llm_fn(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        retries=retries
    )


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.1,
) -> Dict:
    """
    Call LLM and parse response as JSON.

    Automatically strips markdown code fences and extracts JSON object.

    Args:
        system_prompt: System message defining LLM behavior
        user_prompt: User message with task/question
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature

    Returns:
        Parsed JSON dict (empty dict {} on parse failure)

    Example:
        result = call_llm_json(
            system_prompt="Return JSON only.",
            user_prompt='Classify variant. Format: {"classification": "VUS"}',
            temperature=0.0
        )
        print(result["classification"])
    """
    return _call_llm_json_fn(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature
    )
