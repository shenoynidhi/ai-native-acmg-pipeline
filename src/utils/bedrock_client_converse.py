"""
AWS Bedrock Converse API client for ACMG pipeline.

Uses the unified Converse API instead of low-level invoke_model.
Supports reasoning parameters, JSON schema enforcement, and automatic token tracking.

Key Features:
  - Unified API: same code works for all models (no OpenAI vs Nemotron conditionals)
  - Reasoning support: reasoning_effort (GPT-OSS) and max_thinking_tokens (Nemotron)
  - JSON schema enforcement: force models to output valid JSON (eliminates parse errors)
  - Drop-in replacement: call_llm_json() signature identical to bedrock_client.py

Environment Variables:
    AWS_BEARER_TOKEN_BEDROCK: AWS Bedrock API key (required)
    BEDROCK_MODEL: Default model ID (optional, defaults to nemotron-30b)
    BEDROCK_REGION: AWS region (optional, defaults to us-east-1)
"""

import json
import os
import logging
import threading
import time
from typing import Optional, Dict, List
from dotenv import load_dotenv

import boto3
from botocore.config import Config

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# AWS Bedrock API Key (required)
AWS_BEARER_TOKEN = os.getenv("AWS_BEARER_TOKEN_BEDROCK")

# ---------------------------------------------------------------------------
# Rate Limiting - Bedrock Concurrent Request Semaphore
# ---------------------------------------------------------------------------

BEDROCK_MAX_CONCURRENT_REQUESTS = int(os.getenv("BEDROCK_MAX_CONCURRENT_REQUESTS", "40"))
_bedrock_semaphore = threading.Semaphore(BEDROCK_MAX_CONCURRENT_REQUESTS)

logger.info(f"Bedrock Converse semaphore initialized: max {BEDROCK_MAX_CONCURRENT_REQUESTS} concurrent API calls")

# AWS Region
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")

# Default model
DEFAULT_MODEL = os.getenv("BEDROCK_MODEL", "nvidia.nemotron-nano-3-30b")


# ---------------------------------------------------------------------------
# Model Registry
# ---------------------------------------------------------------------------

BEDROCK_MODELS = {
    # NVIDIA Nemotron models
    "nemotron-30b": {
        "id": "nvidia.nemotron-nano-3-30b",
        "name": "NVIDIA Nemotron Nano 3 30B",
        "max_tokens": 4096,
        "supports_system": True,
        "supports_thinking": True,  # Uses max_thinking_tokens
    },
    "nemotron-120b": {
        "id": "nvidia.nemotron-super-3-120b",
        "name": "NVIDIA Nemotron Super 3 120B",
        "max_tokens": 4096,
        "supports_system": True,
        "supports_thinking": True,
    },

    # OpenAI GPT-OSS models (reasoning models)
    "gpt-oss-20b": {
        "id": "openai.gpt-oss-20b-1:0",
        "name": "OpenAI GPT-OSS 20B",
        "max_tokens": 2048,
        "supports_system": True,
        "supports_reasoning_effort": True,  # Test: reasoning_effort parameter
    },
    "gpt-oss-120b": {
        "id": "openai.gpt-oss-120b-1:0",
        "name": "OpenAI GPT-OSS 120B",
        "max_tokens": 4096,
        "supports_system": True,
        "supports_reasoning_effort": True,
    },

    # Moonshot AI Kimi
    "kimi-k2.5": {
        "id": "moonshotai.kimi-k2.5",
        "name": "Moonshot AI Kimi K2.5",
        "max_tokens": 4096,
        "supports_system": True,
    },

    # Google Gemma
    "gemma-27b": {
        "id": "google.gemma-3-27b-it",
        "name": "Google Gemma 3 27B IT",
        "max_tokens": 2048,
        "supports_system": True,
    },

    # Lightning AI GPT-OSS
    "lightning-oss-20b": {
        "id": "lightning-ai/gpt-oss-20b",
        "name": "Lightning OSS 20B",
        "max_tokens": 2048,
        "supports_system": True,
    },
}


# ---------------------------------------------------------------------------
# Bedrock Converse Client
# ---------------------------------------------------------------------------

class BedrockConverseClient:
    """
    AWS Bedrock Converse API client for LLM inference.

    Uses the unified Converse API for all models (no model-specific payload logic).
    Supports reasoning parameters, JSON schema enforcement, and automatic token tracking.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        region: str = BEDROCK_REGION,
        default_model: str = DEFAULT_MODEL
    ):
        """
        Initialize Bedrock Converse client.

        Args:
            api_key: AWS Bedrock API key (uses env var if not provided)
            region: AWS region for Bedrock endpoint
            default_model: Default model ID to use
        """
        self.api_key = api_key or AWS_BEARER_TOKEN
        self.region = region
        self.default_model = default_model

        if not self.api_key:
            raise ValueError(
                "AWS_BEARER_TOKEN_BEDROCK environment variable not set. "
                "Please set it with your Bedrock API key."
            )

        # Configure boto3 client with connection pooling optimization
        config = Config(
            region_name=self.region,
            max_pool_connections=500,  # Up from 10 (default) - critical for parallel agents
            retries={'max_attempts': 3, 'mode': 'adaptive'},
            read_timeout=600,  # 10 minutes (was 300s - increased for rate-limited retries)
            connect_timeout=10  # Fast connection timeout
        )

        # Create Bedrock runtime client with bearer token
        self.client = boto3.client(
            'bedrock-runtime',
            config=config,
            aws_access_key_id='BEARER',  # Special value for bearer token auth
            aws_secret_access_key=self.api_key,
            aws_session_token=None
        )

        logger.info(f"Bedrock Converse client initialized (region: {region}, default_model: {default_model})")

    def get_model_info(self, model_id: str) -> Dict:
        """Get model configuration by ID or short name."""
        # Check if it's a short name
        if model_id in BEDROCK_MODELS:
            return BEDROCK_MODELS[model_id]

        # Check if it's a full model ID
        for short_name, info in BEDROCK_MODELS.items():
            if info["id"] == model_id:
                return info

        # Not found - return default config
        logger.warning(f"Unknown model {model_id}, using default config")
        return {
            "id": model_id,
            "name": model_id,
            "max_tokens": 2048,
            "supports_system": True,
        }

    def converse(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        system_prompt: Optional[str] = None,
        json_schema: Optional[Dict] = None,
        reasoning_effort: Optional[str] = None,
        max_thinking_tokens: Optional[int] = None,
    ) -> Dict:
        """
        Generate completion using Bedrock Converse API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model ID or short name (uses default if not provided)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate (total: thinking + answer)
            system_prompt: Optional system prompt (separate from messages)
            json_schema: Optional JSON schema to enforce structured output
            reasoning_effort: Reasoning effort for GPT-OSS models ("low"/"medium"/"high")
            max_thinking_tokens: Max thinking tokens for Nemotron models (e.g., 3072)

        Returns:
            Dict with 'text', 'input_tokens', 'output_tokens', 'reasoning_tokens' (if applicable)

        Raises:
            Exception: If API call fails after retries
        """
        model_id = model or self.default_model
        model_info = self.get_model_info(model_id)

        # Build request
        request = {
            "modelId": model_info["id"],
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": min(max_tokens, model_info.get("max_tokens", 2048)),
                "temperature": temperature,
            }
        }

        # Add system prompt if provided
        if system_prompt:
            request["system"] = [{"text": system_prompt}]

        # Build additionalModelRequestFields (model-specific parameters)
        additional_fields = {}

        # Add reasoning parameters based on model type
        if reasoning_effort and model_info.get("supports_reasoning_effort"):
            # GPT-OSS models use reasoning_effort
            additional_fields["reasoning_effort"] = reasoning_effort
            logger.debug(f"Using reasoning_effort={reasoning_effort} for {model_info['id']}")

        if max_thinking_tokens and model_info.get("supports_thinking"):
            # Nemotron models use max_thinking_tokens
            additional_fields["max_thinking_tokens"] = max_thinking_tokens
            logger.debug(f"Using max_thinking_tokens={max_thinking_tokens} for {model_info['id']}")

        # Add JSON schema if provided (force structured output)
        if json_schema:
            additional_fields["response_format"] = {
                "type": "json_schema",
                "json_schema": json_schema
            }
            logger.debug(f"Enforcing JSON schema: {json_schema.get('name', 'unnamed')}")

        # Add additionalModelRequestFields to request if any
        if additional_fields:
            request["additionalModelRequestFields"] = additional_fields

        try:
            # Acquire semaphore slot (limits concurrent Bedrock API calls)
            semaphore_wait_start = time.time()

            with _bedrock_semaphore:
                # Log if we had to wait for a semaphore slot (> 1 second)
                wait_time = time.time() - semaphore_wait_start
                if wait_time > 1.0:
                    logger.debug(f"Waited {wait_time:.1f}s for Bedrock semaphore slot")

                # Now make the actual Bedrock Converse API call
                api_call_start = time.time()
                response = self.client.converse(**request)
                api_call_time = time.time() - api_call_start

                # Log slow API calls (> 5 seconds)
                if api_call_time > 5.0:
                    logger.debug(f"Slow Bedrock API call: {api_call_time:.1f}s")

            # Extract token usage
            usage = response.get("usage", {})
            input_tokens = usage.get("inputTokens", 0)
            output_tokens = usage.get("outputTokens", 0)

            # Some models return reasoning tokens separately
            reasoning_tokens = usage.get("reasoningTokens", 0) or usage.get("thinkingTokens", 0)

            # Log token usage
            if reasoning_tokens > 0:
                logger.debug(f"Token usage: {input_tokens} in, {output_tokens} out, {reasoning_tokens} reasoning")
            else:
                logger.debug(f"Token usage: {input_tokens} in, {output_tokens} out")

            # Log tokens to tracking system
            try:
                from src.utils.token_tracker import log_tokens
                # Try to get session_id from context
                import inspect
                frame = inspect.currentframe()
                session_id = "unknown"
                for _ in range(10):
                    if frame is None:
                        break
                    local_vars = frame.f_locals
                    if "session_id" in local_vars:
                        session_id = local_vars["session_id"]
                        break
                    elif "state" in local_vars and isinstance(local_vars["state"], dict):
                        session_id = local_vars["state"].get("session_id", "unknown")
                        if session_id != "unknown":
                            break
                    frame = frame.f_back

                log_tokens(
                    session_id=session_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    model=model_id,
                    agent="converse_client"
                )
            except Exception as e:
                logger.debug(f"Failed to log token usage: {e}")

            # Extract generated text
            output = response.get("output", {})
            message = output.get("message", {})
            content_blocks = message.get("content", [])

            if not content_blocks:
                logger.error(f"No content in response: {response}")
                raise ValueError("Empty response from Bedrock Converse API")

            # Extract text from content blocks
            # For reasoning models, response structure is:
            #   content[0] = {"reasoningContent": {"reasoningText": {"text": "..."}}}  # Internal thinking
            #   content[1] = {"text": "..."}  # Final answer
            # For non-reasoning: content[0] = {"text": "..."}
            text = ""
            reasoning_text_content = ""

            for block in content_blocks:
                # Check for final answer text (skip reasoning content)
                if "text" in block:
                    text = block["text"]
                    break  # Found final answer, use it
                # Track reasoning content if present (fallback)
                elif "reasoningContent" in block:
                    reasoning_text_content = block["reasoningContent"]["reasoningText"]["text"]

            # If no text block found, use reasoning as fallback (shouldn't happen with reasoning_effort)
            if not text and reasoning_text_content:
                logger.warning(f"No final answer in response, using reasoning text as fallback")
                text = reasoning_text_content

            if not text:
                logger.error(f"No text content in response: {content_blocks}")
                raise ValueError("No text content in Bedrock response")

            return {
                "text": text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
                "stop_reason": response.get("stopReason", "unknown"),
            }

        except Exception as e:
            logger.error(f"Bedrock Converse API call failed: {e}")
            raise

    def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.0,
        model: Optional[str] = None,
        retries: int = 3,
        reasoning_effort: Optional[str] = None,
        max_thinking_tokens: Optional[int] = None,
    ) -> str:
        """
        Simple LLM call with system + user prompts (matches legacy interface).

        Args:
            system_prompt: System message
            user_prompt: User message
            max_tokens: Max tokens to generate
            temperature: Sampling temperature
            model: Model ID (optional)
            retries: Number of retry attempts
            reasoning_effort: Reasoning effort for GPT-OSS ("low"/"medium"/"high")
            max_thinking_tokens: Max thinking tokens for Nemotron (e.g., 3072)

        Returns:
            Generated text response
        """
        messages = [
            {"role": "user", "content": [{"text": user_prompt}]}
        ]

        for attempt in range(1, retries + 1):
            try:
                result = self.converse(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_prompt=system_prompt,
                    reasoning_effort=reasoning_effort,
                    max_thinking_tokens=max_thinking_tokens,
                )
                return result["text"]
            except Exception as e:
                logger.warning(f"LLM call attempt {attempt}/{retries} failed: {e}")
                if attempt == retries:
                    raise

        return ""

    def _extract_balanced_json(self, text: str) -> Optional[str]:
        """
        Extract the first balanced JSON object by tracking brace depth.
        Handles cases where regex captures incomplete JSON due to truncation.

        Args:
            text: Text containing JSON object

        Returns:
            Balanced JSON string or None if not found
        """
        start_idx = text.find('{')
        if start_idx == -1:
            return None

        depth = 0
        in_string = False
        escape_next = False

        for i in range(start_idx, len(text)):
            char = text[i]

            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if not in_string:
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        # Found complete JSON object
                        return text[start_idx:i+1]

        return None

    def call_llm_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.0,
        model: Optional[str] = None,
        json_schema: Optional[Dict] = None,
        reasoning_effort: Optional[str] = None,
        max_thinking_tokens: Optional[int] = None,
    ) -> Dict:
        """
        Call LLM and parse response as JSON.

        With JSON schema enforcement, parse errors are eliminated (model is forced
        to output valid JSON). Without schema, falls back to regex extraction.

        Args:
            system_prompt: System message
            user_prompt: User message
            max_tokens: Max tokens to generate
            temperature: Sampling temperature
            model: Model ID (optional)
            json_schema: Optional JSON schema to enforce (eliminates parse errors)
            reasoning_effort: Reasoning effort for GPT-OSS ("low"/"medium"/"high")
            max_thinking_tokens: Max thinking tokens for Nemotron (e.g., 3072)

        Returns:
            Parsed JSON dict (empty dict on failure without schema)
        """
        import re

        raw = self.call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
            reasoning_effort=reasoning_effort,
            max_thinking_tokens=max_thinking_tokens,
        )

        # If JSON schema was enforced, response should be valid JSON (no extraction needed)
        if json_schema:
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                logger.error(f"JSON schema enforcement failed: {e}\nRaw: {raw[:500]}")
                # Try extraction as fallback
                pass

        # Fallback: manual JSON extraction (for models without schema support)
        # Strip markdown code fences
        clean = re.sub(r"```json\s*", "", raw)
        clean = re.sub(r"```\s*", "", clean).strip()

        # Strip <reasoning> tags (common in reasoning models like DeepSeek R1, o1, etc.)
        clean = re.sub(r"<reasoning>.*?</reasoning>", "", clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r"</?reasoning>", "", clean, flags=re.IGNORECASE).strip()

        # Strip any text BEFORE the first '{' (handles models that reason before JSON)
        first_brace = clean.find('{')
        if first_brace > 0:
            pre_json_text = clean[:first_brace].strip()
            if pre_json_text:
                logger.debug(f"Stripped pre-JSON text: {pre_json_text[:100]}...")
            clean = clean[first_brace:]

        # Extract first complete JSON object using non-greedy match
        match = re.search(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", clean, re.DOTALL)
        if match:
            json_str = match.group()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                # Try to find a complete JSON by balancing braces
                try:
                    balanced_json = self._extract_balanced_json(clean)
                    if balanced_json:
                        return json.loads(balanced_json)
                except:
                    pass

                # IMPROVED ERROR LOGGING: Show more context
                logger.error(f"JSON parse failed: {e}")
                logger.error(f"Raw response length: {len(raw)} chars")

                # Show text before JSON if it exists
                if '{' in raw:
                    json_start = raw.find('{')
                    if json_start > 0:
                        logger.error(f"Text before JSON: {raw[:json_start][:200]}")

                # Show attempted JSON (first 500 chars)
                logger.error(f"Attempted JSON: {json_str[:500]}")

                # Check for truncation indicators
                if json_str.rstrip().endswith(('",', ',"', '"', ',')):
                    logger.warning("Response appears truncated (ends mid-structure). Consider increasing max_tokens.")

        logger.warning(f"No valid JSON found in response (length: {len(raw)})")
        return {}


# ---------------------------------------------------------------------------
# Global client instance (lazy initialization)
# ---------------------------------------------------------------------------

_bedrock_converse_client: Optional[BedrockConverseClient] = None


def get_bedrock_converse_client() -> BedrockConverseClient:
    """
    Get or create global Bedrock Converse client instance.

    Returns:
        Shared BedrockConverseClient instance
    """
    global _bedrock_converse_client
    if _bedrock_converse_client is None:
        _bedrock_converse_client = BedrockConverseClient()
    return _bedrock_converse_client


# ---------------------------------------------------------------------------
# Legacy API compatibility (drop-in replacement for bedrock_client.py)
# ---------------------------------------------------------------------------

def call_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2000,
    temperature: float = 0.0,
    retries: int = 3,
    reasoning_effort: Optional[str] = None,
    max_thinking_tokens: Optional[int] = None,
) -> str:
    """
    Legacy API: Call LLM with system + user prompts.

    Drop-in replacement for src/utils/llm_client.py::call_llm()

    Args:
        reasoning_effort: Reasoning effort for GPT-OSS ("low"/"medium"/"high")
        max_thinking_tokens: Max thinking tokens for Nemotron (e.g., 3072)
    """
    client = get_bedrock_converse_client()
    return client.call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        retries=retries,
        reasoning_effort=reasoning_effort,
        max_thinking_tokens=max_thinking_tokens,
    )


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2000,
    temperature: float = 0.0,
    model_override: Optional[str] = None,
    json_schema: Optional[Dict] = None,
    reasoning_effort: Optional[str] = None,
    max_thinking_tokens: Optional[int] = None,
) -> Dict:
    """
    Legacy API: Call LLM and parse response as JSON.

    Drop-in replacement for src/utils/llm_client.py::call_llm_json()

    Args:
        model_override: Override default model (e.g., "openai.gpt-oss-20b-1:0")
        json_schema: Optional JSON schema to enforce (eliminates parse errors)
        reasoning_effort: Reasoning effort for GPT-OSS ("low"/"medium"/"high")
        max_thinking_tokens: Max thinking tokens for Nemotron (e.g., 3072)
    """
    client = get_bedrock_converse_client()

    return client.call_llm_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        model=model_override,
        json_schema=json_schema,
        reasoning_effort=reasoning_effort,
        max_thinking_tokens=max_thinking_tokens,
    )


# ---------------------------------------------------------------------------
# Model listing (for API endpoints)
# ---------------------------------------------------------------------------

def list_available_models() -> List[Dict]:
    """
    Get list of available Bedrock models.

    Returns:
        List of model info dicts with id, name, max_tokens
    """
    return [
        {
            "id": info["id"],
            "name": info["name"],
            "short_name": short_name,
            "max_tokens": info["max_tokens"],
            "supports_reasoning": info.get("supports_reasoning_effort") or info.get("supports_thinking"),
        }
        for short_name, info in BEDROCK_MODELS.items()
    ]

