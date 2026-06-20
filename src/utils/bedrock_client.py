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
AWS_BEARER_TOKEN = os.getenv(
    "AWS_BEARER_TOKEN_BEDROCK",
    "YOUR_BEDROCK_TOKEN_HERE="
)

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
        "max_tokens": 2048,
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

        # Configure boto3 client
        config = Config(
            region_name=self.region,
            retries={'max_attempts': 3, 'mode': 'adaptive'}
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
        temperature: float = 0.1,
        max_tokens: int = 1000,
        system_prompt: Optional[str] = None,
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

        # Build request payload
        payload = {
            "messages": request_messages,
            "temperature": temperature,
            "max_tokens": min(max_tokens, model_info.get("max_tokens", 2048)),
        }

        try:
            # Call Bedrock API
            response = self.client.invoke_model(
                modelId=model_info["id"],
                body=json.dumps(payload),
                contentType="application/json",
                accept="application/json"
            )

            # Parse response
            response_body = json.loads(response['body'].read())

            # Extract generated text (format varies by model)
            if "choices" in response_body:
                # OpenAI-style response
                return response_body["choices"][0]["message"]["content"]
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
        temperature: float = 0.1,
        model: Optional[str] = None,
        retries: int = 3,
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
                    max_tokens=max_tokens
                )
            except Exception as e:
                logger.warning(f"LLM call attempt {attempt}/{retries} failed: {e}")
                if attempt == retries:
                    raise

        return ""

    def call_llm_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.1,
        model: Optional[str] = None,
    ) -> Dict:
        """
        Call LLM and parse response as JSON.

        Strips markdown fences and extracts first JSON object.

        Args:
            system_prompt: System message
            user_prompt: User message
            max_tokens: Max tokens to generate
            temperature: Sampling temperature
            model: Model ID (optional)

        Returns:
            Parsed JSON dict (empty dict on failure)
        """
        import re

        raw = self.call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model
        )

        # Strip markdown code fences
        clean = re.sub(r"```json\s*", "", raw)
        clean = re.sub(r"```\s*", "", clean).strip()

        # Extract first JSON object
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse failed: {e}\nRaw: {raw[:300]}")

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
    temperature: float = 0.1,
    retries: int = 3,
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
        retries=retries
    )


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.1,
) -> Dict:
    """
    Legacy API: Call LLM and parse response as JSON.

    Drop-in replacement for src/utils/llm_client.py::call_llm_json()
    """
    client = get_bedrock_client()
    return client.call_llm_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature
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
