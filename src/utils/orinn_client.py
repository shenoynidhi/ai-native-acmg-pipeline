"""
ORINN API Client for Medical-Trained LLM

ORINN specializes in medical/clinical NLP tasks including:
- HPO term extraction from clinical notes
- Disease entity recognition
- Medical text interpretation

Documentation: https://platform.orinn.ai/docs
API Key: Set ORINN_API_KEY in .env.aws (defaults to hardcoded value)
Base URL: Set ORINN_ENDPOINT in .env.aws (defaults to https://api-call.orinn.ai/v1)
         Note: /chat/completions is auto-appended if not present (like LangChain's ChatOpenAI)
"""

import logging
import os
import json
from typing import Optional
import requests

from src.utils.logging_config import get_user_friendly_logger

logger = get_user_friendly_logger('orinn_client')

# ORINN Configuration - load from environment or use default
# Note: Environment variables are loaded by src/config.py at startup
ORINN_API_KEY = os.getenv("ORINN_API_KEY", "sk-orinn-JijyNkh_K6W1Rh7zygGVFL4OjpUvEUISHupcW23NQho")
ORINN_BASE_URL = os.getenv("ORINN_ENDPOINT", "https://api-call.orinn.ai/v1")

# Auto-append /chat/completions if not already present (like LangChain does)
if not ORINN_BASE_URL.endswith("/chat/completions"):
    ORINN_ENDPOINT = f"{ORINN_BASE_URL}/chat/completions"
else:
    ORINN_ENDPOINT = ORINN_BASE_URL

# Validate API key is set
if not ORINN_API_KEY or ORINN_API_KEY == "your-orinn-api-key-here":
    logger.warning(
        "ORINN_API_KEY not configured. Set it in .env.aws file. "
        "Get your key from: https://platform.orinn.ai/"
    )


def call_orinn(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    max_tokens: int = 4000,
    retries: int = 2,
) -> Optional[str]:
    """
    Call ORINN medical-trained LLM API with retry logic.

    Args:
        system_prompt: System instructions
        user_prompt: User query
        temperature: Sampling temperature (0.0 for deterministic)
        max_tokens: Maximum response tokens
        retries: Number of retry attempts on failure (default: 2)

    Returns:
        Response text or None on error (after all retries exhausted)

    Note:
        Automatically retries on 500 errors (ORINN server bugs) and timeouts.
        Waits 1-2s between retries to avoid overwhelming the API.
    """
    # Validate inputs (ORINN API is sensitive to None/empty values)
    if not system_prompt or not isinstance(system_prompt, str):
        logger.error("call_orinn: system_prompt is empty or invalid")
        return None

    if not user_prompt or not isinstance(user_prompt, str):
        logger.error("call_orinn: user_prompt is empty or invalid")
        return None

    # Ensure prompts are strings and not just whitespace
    system_prompt = str(system_prompt).strip()
    user_prompt = str(user_prompt).strip()

    if not system_prompt or not user_prompt:
        logger.error("call_orinn: prompts are empty after stripping whitespace")
        return None

    headers = {
        "Authorization": f"Bearer {ORINN_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "Orinn-1.7",  # ORINN's clinical model (matches LangChain implementation)
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # Retry loop for transient ORINN server errors (500, NoneType bugs)
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                ORINN_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]

        except requests.exceptions.Timeout:
            logger.error(f"ORINN API timeout after 30s (attempt {attempt + 1}/{retries + 1})")
            last_error = "timeout"
            if attempt < retries:
                import time
                time.sleep(1)  # Wait 1s before retry
                continue
            return None

        except requests.exceptions.HTTPError as e:
            # Check if it's a retryable 500 error
            is_500_error = e.response.status_code == 500
            logger.error(
                f"ORINN API HTTP error: {e.response.status_code} - {e.response.text}\n"
                f"Request payload preview: system_prompt={system_prompt[:100]}..., "
                f"user_prompt={user_prompt[:100]}... "
                f"(attempt {attempt + 1}/{retries + 1})"
            )
            last_error = f"HTTP {e.response.status_code}"

            # Retry on 500 errors (server-side bugs)
            if is_500_error and attempt < retries:
                import time
                time.sleep(2)  # Wait 2s before retry on 500 errors
                continue
            return None

        except Exception as e:
            logger.error(f"ORINN API call failed: {e} (attempt {attempt + 1}/{retries + 1})")
            last_error = str(e)
            if attempt < retries:
                import time
                time.sleep(1)
                continue
            return None

    logger.error(f"ORINN API failed after {retries + 1} attempts. Last error: {last_error}")
    return None


def call_orinn_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    max_tokens: int = 4000,
) -> dict:
    """
    Call ORINN and parse JSON response.

    Returns:
        Parsed JSON dict or {"error": "..."} on failure
    """
    response = call_orinn(system_prompt, user_prompt, temperature, max_tokens)

    if not response:
        return {"error": "ORINN API call failed (no response)"}

    try:
        # Try direct JSON parse
        return json.loads(response)
    except json.JSONDecodeError:
        # Try extracting JSON from markdown code fence
        if "```json" in response:
            start = response.find("```json") + 7
            end = response.find("```", start)
            json_str = response[start:end].strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        logger.error(f"ORINN response not valid JSON: {response[:200]}")
        return {"error": "ORINN response not valid JSON", "raw_response": response}

