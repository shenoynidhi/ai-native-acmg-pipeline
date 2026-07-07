"""
AWS Bedrock LLM client for ACMG pipeline.

Replaces vLLM with AWS Bedrock API for all agent LLM calls.
Supports multiple models: Nemotron 30B, Nemotron 120B, ChatGPT-OSS 20B, ChatGPT-OSS 120B.

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

# Limit concurrent Bedrock API calls to prevent throttling
# Based on testing:
#   - 80 concurrent (16 workers × 5 LLM agents) = 1,000+ throttling errors
#   - 50 concurrent (10 workers × 5 LLM agents) = 23 throttling errors (borderline)
#   - 40 concurrent (8 workers × 5 LLM agents) = 0-5 errors (stable)
#
# This semaphore allows increasing NUM_VARIANT_WORKERS while keeping Bedrock calls limited.
# Example: 16 workers + Semaphore(40) = more CPU utilization, no throttling
BEDROCK_MAX_CONCURRENT_REQUESTS = int(os.getenv("BEDROCK_MAX_CONCURRENT_REQUESTS", "40"))

_bedrock_semaphore = threading.Semaphore(BEDROCK_MAX_CONCURRENT_REQUESTS)

logger.info(f"Bedrock semaphore initialized: max {BEDROCK_MAX_CONCURRENT_REQUESTS} concurrent API calls")

# AWS Region
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")

# Default model
DEFAULT_MODEL = os.getenv("BEDROCK_MODEL", "google.gemma-3-12b-it")


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
    },
    "nemotron-120b": {
        "id": "nvidia.nemotron-super-3-120b",
        "name": "NVIDIA Nemotron Super 3 120B",
        "max_tokens": 4096,
        "supports_system": True,
    },

    # OpenAI GPT-OSS models
    "gpt-oss-20b": {
        "id": "openai.gpt-oss-20b-1:0",
        "name": "OpenAI GPT-OSS 20B",
        "max_tokens": 4096,  # FIXED: was 2048, causing truncation with reasoning_effort
        "supports_system": True,
    },
    "gpt-oss-120b": {
        "id": "openai.gpt-oss-120b-1:0",
        "name": "OpenAI GPT-OSS 120B",
        "max_tokens": 4096,
        "supports_system": True,
    },

    # Moonshot AI Kimi
    "kimi-k2.5": {
        "id": "moonshotai.kimi-k2.5",
        "name": "Moonshot AI Kimi K2.5",
        "max_tokens": 4096,
        "supports_system": True,
    },

    # Google Gemma
    "gemma-12b": {
        "id": "google.gemma-3-12b-it",
        "name": "Google Gemma 3 12B IT",
        "max_tokens": 4096,
        "supports_system": True,
    },
    "gemma-27b": {
        "id": "google.gemma-3-27b-it",
        "name": "Google Gemma 3 27B IT",
        "max_tokens": 4096,
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
# Bedrock Client
# ---------------------------------------------------------------------------

class BedrockClient:
    """
    AWS Bedrock client for LLM inference.

    Supports chat completions with system prompts, temperature control,
    and automatic retry logic.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        region: str = BEDROCK_REGION,
        default_model: str = DEFAULT_MODEL
    ):
        """
        Initialize Bedrock client.

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
            max_pool_connections=9000,  # Up from 10 (default) - critical for parallel agents
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

        logger.info(f"Bedrock client initialized (region: {region}, default_model: {default_model})")

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

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        system_prompt: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        max_thinking_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate chat completion using Bedrock.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model ID or short name (uses default if not provided)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            system_prompt: Optional system prompt (prepended to messages)

        Returns:
            Generated text response

        Raises:
            Exception: If API call fails after retries
        """
        model_id = model or self.default_model
        model_info = self.get_model_info(model_id)

        # Build request messages
        request_messages = []

        # Add system prompt if provided and supported
        if system_prompt and model_info.get("supports_system"):
            request_messages.append({
                "role": "system",
                "content": system_prompt
            })

        # Add conversation messages
        request_messages.extend(messages)

        # Detect OpenAI models - they use different request format
        is_openai_model = "openai" in model_info["id"].lower()

        # Build request payload
        payload = {
            "messages": request_messages,
            "temperature": temperature,
        }

        # OpenAI models use max_completion_tokens, others use max_tokens
        token_limit = min(max_tokens, model_info.get("max_tokens", 2048))
        if is_openai_model:
            payload["max_completion_tokens"] = token_limit
            payload["stream"] = False  # Required for InvokeModel API
            # Add reasoning_effort if provided (GPT-OSS models)
            if reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
        else:
            payload["max_tokens"] = token_limit
            # Add max_thinking_tokens if provided (Nemotron models)
            if max_thinking_tokens:
                payload["max_thinking_tokens"] = max_thinking_tokens

        try:
            # Acquire semaphore slot (limits concurrent Bedrock API calls)
            # This blocks if BEDROCK_MAX_CONCURRENT_REQUESTS are already in flight
            semaphore_wait_start = time.time()

            with _bedrock_semaphore:
                # Log if we had to wait for a semaphore slot (> 1 second)
                wait_time = time.time() - semaphore_wait_start
                if wait_time > 1.0:
                    logger.debug(f"Waited {wait_time:.1f}s for Bedrock semaphore slot")

                # Now make the actual Bedrock API call
                api_call_start = time.time()
                response = self.client.invoke_model(
                    modelId=model_info["id"],
                    body=json.dumps(payload),
                    contentType="application/json",
                    accept="application/json"
                )
                api_call_time = time.time() - api_call_start

                # Log slow API calls (> 5 seconds)
                if api_call_time > 5.0:
                    logger.debug(f"Slow Bedrock API call: {api_call_time:.1f}s")

            # Parse response
            response_body = json.loads(response['body'].read())

            # DEBUG: Log response structure for token extraction debugging
            logger.debug(f"[DEBUG] Response keys: {list(response_body.keys())}")
            logger.debug(f"[DEBUG] Usage field present: {'usage' in response_body}")
            if "usage" in response_body:
                logger.debug(f"[DEBUG] Usage data: {response_body.get('usage')}")

            # Extract token usage (if available)
            input_tokens = 0
            output_tokens = 0
            if "usage" in response_body:
                # Standard usage format
                usage = response_body["usage"]
                input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
                logger.debug(f"[DEBUG] Extracted tokens: {input_tokens} in, {output_tokens} out")
            elif "ResponseMetadata" in response and "usage" in response["ResponseMetadata"]:
                # AWS-specific metadata
                usage = response["ResponseMetadata"]["usage"]
                input_tokens = usage.get("inputTokens", 0)
                output_tokens = usage.get("outputTokens", 0)

            # Log token usage if available
            if input_tokens > 0 or output_tokens > 0:
                try:
                    from src.utils.token_tracker import log_tokens
                    # Try to get session_id from context (if available)
                    import inspect
                    frame = inspect.currentframe()
                    session_id = "unknown"
                    # Walk up the stack to find session_id
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
                        model=model_id,  # Use model_id from function scope
                        agent="bedrock_client"
                    )
                    logger.debug(f"[{session_id}] Token usage: {input_tokens} in, {output_tokens} out")
                except Exception as e:
                    logger.debug(f"Failed to log token usage: {e}")

            # Extract generated text (format varies by model)
            if "choices" in response_body:
                # OpenAI-style response
                choice = response_body["choices"][0]
                message = choice.get("message", {})

                # CRITICAL: reasoning models may include reasoning_content field
                # Structure: {"message": {"reasoning_content": "...", "content": "final answer"}}
                # We want the FINAL ANSWER (content), not the reasoning
                content = message.get("content", "")

                # DEBUG: Log if reasoning_content exists
                if "reasoning_content" in message:
                    logger.debug(f"Model included reasoning_content (length: {len(message['reasoning_content'])})")

                return content
            elif "content" in response_body:
                # Direct content response
                return response_body["content"]
            elif "output" in response_body:
                # Some models use 'output' field
                return response_body["output"]
            else:
                logger.error(f"Unexpected response format: {response_body}")
                raise ValueError("Could not parse model response")

        except Exception as e:
            logger.error(f"Bedrock API call failed: {e}")
            raise

    def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
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

        Returns:
            Generated text response
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        for attempt in range(1, retries + 1):
            try:
                return self.chat_completion(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort,
                    max_thinking_tokens=max_thinking_tokens
                )
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
        reasoning_effort: Optional[str] = None,
        max_thinking_tokens: Optional[int] = None,
        debug_dump: bool = False,  # NEW: Enable raw output dump for debugging
    ) -> Dict:
        """
        Call LLM and parse response as JSON.

        Strips markdown fences, reasoning tags, and extracts first JSON object.
        Handles common LLM output formats:
        - Markdown code fences: ```json ... ```
        - Reasoning tags: <reasoning>...</reasoning>{...}
        - Truncated JSON (due to max_tokens limit)

        Args:
            system_prompt: System message
            user_prompt: User message
            max_tokens: Max tokens to generate (default 2000, increased from 1000 to prevent JSON truncation)
            temperature: Sampling temperature
            model: Model ID (optional)
            debug_dump: If True, write raw response to debug_llm_outputs/ dir

        Returns:
            Parsed JSON dict (empty dict on failure)
        """
        import re

        raw = self.call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
            reasoning_effort=reasoning_effort,
            max_thinking_tokens=max_thinking_tokens
        )

        # We'll decide whether to dump AFTER parsing attempt
        parsing_failed = False

        # Strip markdown code fences
        clean = re.sub(r"```json\s*", "", raw)
        clean = re.sub(r"```\s*", "", clean).strip()

        # Strip <reasoning> tags (common in reasoning models like DeepSeek R1, o1, etc.)
        # These models output <reasoning>...</reasoning> before the actual JSON
        clean = re.sub(r"<reasoning>.*?</reasoning>", "", clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r"</?reasoning>", "", clean, flags=re.IGNORECASE).strip()

        # FIX: Strip any text BEFORE the first '{' (handles models that reason before JSON)
        # Example: "Let me think. The variant shows... {\"key\": \"value\"}"
        # Becomes: "{\"key\": \"value\"}"
        first_brace = clean.find('{')
        if first_brace > 0:
            pre_json_text = clean[:first_brace].strip()
            if pre_json_text:
                logger.debug(f"Stripped pre-JSON text: {pre_json_text[:100]}...")
            clean = clean[first_brace:]

        # Extract first complete JSON object using non-greedy match
        # This prevents grabbing text after the JSON
        match = re.search(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", clean, re.DOTALL)
        if match:
            json_str = match.group()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                # Try to find a complete JSON by balancing braces
                # (handles cases where regex grabbed incomplete JSON)
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
        parsing_failed = True

        # DEBUG: Dump raw response if parsing failed
        if parsing_failed or debug_dump:
            import os
            import time
            debug_dir = "debug_llm_outputs"
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = int(time.time() * 1000)
            debug_file = os.path.join(debug_dir, f"llm_raw_{timestamp}.txt")
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(f"=== CONFIG ===\n")
                f.write(f"Model: {model or self.default_model}\n")
                f.write(f"Max tokens: {max_tokens}\n")
                f.write(f"Reasoning effort: {reasoning_effort}\n")
                f.write(f"Max thinking tokens: {max_thinking_tokens}\n\n")
                f.write(f"=== SYSTEM PROMPT ===\n{system_prompt[:500]}...\n\n")
                f.write(f"=== USER PROMPT ===\n{user_prompt[:500]}...\n\n")
                f.write(f"=== RAW RESPONSE (length: {len(raw)}) ===\n{raw}\n")
            logger.warning(f"DEBUG: Raw LLM output dumped to {debug_file}")

        return {}


# ---------------------------------------------------------------------------
# Global client instance (lazy initialization)
# ---------------------------------------------------------------------------

_bedrock_client: Optional[BedrockClient] = None


def get_bedrock_client() -> BedrockClient:
    """
    Get or create global Bedrock client instance.

    Returns:
        Shared BedrockClient instance
    """
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = BedrockClient()
    return _bedrock_client


# ---------------------------------------------------------------------------
# Legacy API compatibility (drop-in replacement for llm_client.py)
# ---------------------------------------------------------------------------

def call_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.0,
    retries: int = 3,
    reasoning_effort: Optional[str] = None,
    max_thinking_tokens: Optional[int] = None,
) -> str:
    """
    Legacy API: Call LLM with system + user prompts.

    Drop-in replacement for src/utils/llm_client.py::call_llm()
    """
    client = get_bedrock_client()
    return client.call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        retries=retries,
        reasoning_effort=reasoning_effort,
        max_thinking_tokens=max_thinking_tokens
    )


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.0,
    model_override: str = None,
    reasoning_effort: Optional[str] = None,
    max_thinking_tokens: Optional[int] = None,
    debug_dump: bool = False,
) -> Dict:
    """
    Legacy API: Call LLM and parse response as JSON.

    Drop-in replacement for src/utils/llm_client.py::call_llm_json()

    Args:
        model_override: Override default model (e.g., "openai.gpt-oss-20b-1.0")
    """
    client = get_bedrock_client()

    return client.call_llm_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        model=model_override,  # Pass through (None means use default)
        reasoning_effort=reasoning_effort,
        max_thinking_tokens=max_thinking_tokens,
        debug_dump=debug_dump
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
        }
        for short_name, info in BEDROCK_MODELS.items()
    ]

