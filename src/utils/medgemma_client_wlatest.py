"""
MedGemma API Client for Medical LLM (Google's MedGemma 1.5-4B)

MedGemma is a medical-domain fine-tuned version of Gemma optimized for:
- Clinical variant interpretation
- Medical reasoning
- Healthcare NLP tasks

API: http://YOUR_SERVER:8080/api/v1/chat
Model: google/medgemma-1.5-4b-it
"""

import logging
import os
import json
import time
from typing import Optional
import requests

from src.utils.logging_config import get_user_friendly_logger

logger = get_user_friendly_logger('medgemma_client')

# MedGemma Configuration - load from environment
MEDGEMMA_API_KEY = os.getenv("MEDGEMMA_API_KEY", "")
MEDGEMMA_BASE_URL = os.getenv("MEDGEMMA_BASE_URL", "http://34.202.249.175:8080/api/v1")
MEDGEMMA_MODEL = os.getenv("MEDGEMMA_MODEL", "google/medgemma-1.5-4b-it")

# Construct full endpoint
MEDGEMMA_ENDPOINT = f"{MEDGEMMA_BASE_URL}/chat"

# Validate API key is set
if not MEDGEMMA_API_KEY:
    logger.warning(
        "MEDGEMMA_API_KEY not configured. Set it in .env.aws file. "
        "⚠️ DO NOT commit API keys to git!"
    )


def call_medgemma(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 512,
    retries: int = 2,
) -> Optional[str]:
    """
    Call MedGemma medical-trained LLM API with retry logic.

    Args:
        system_prompt: System instructions
        user_prompt: User query
        temperature: Sampling temperature (0.2 default for medical tasks)
        max_tokens: Maximum response tokens (512 default, max ~2048)
        retries: Number of retry attempts on failure (default: 2)

    Returns:
        Response text or None on error (after all retries exhausted)

    Note:
        - Uses OpenAI-compatible API format
        - Model: google/medgemma-1.5-4b-it
        - Automatically retries on 500 errors and timeouts
        - Waits 1-2s between retries to avoid overwhelming the API
    """
    # Validate inputs
    if not system_prompt or not isinstance(system_prompt, str):
        logger.error("call_medgemma: system_prompt is empty or invalid")
        return None

    if not user_prompt or not isinstance(user_prompt, str):
        logger.error("call_medgemma: user_prompt is empty or invalid")
        return None

    # Ensure prompts are strings and not just whitespace
    system_prompt = str(system_prompt).strip()
    user_prompt = str(user_prompt).strip()

    if not system_prompt or not user_prompt:
        logger.error("call_medgemma: prompts are empty after stripping whitespace")
        return None

    # Check API key is set
    if not MEDGEMMA_API_KEY:
        logger.error("call_medgemma: MEDGEMMA_API_KEY not set in environment")
        return None

    headers = {
        "x-api-key": MEDGEMMA_API_KEY,
        "Content-Type": "application/json",
    }

    # OpenAI-compatible format with model parameter (REQUIRED)
    payload = {
        "model": MEDGEMMA_MODEL,  # CRITICAL: Must include model
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # Retry loop for transient server errors
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                MEDGEMMA_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=600.0,  # 10 min timeout (matches colleague's spec)
            )
            response.raise_for_status()
            result = response.json()

            # OpenAI-compatible response format
            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                if content:
                    return content
                else:
                    logger.error("MedGemma returned empty content")
                    return None
            else:
                logger.error(f"Unexpected MedGemma response format: {list(result.keys())}")
                return None

        except requests.exceptions.Timeout:
            logger.error(f"MedGemma API timeout after 600s (attempt {attempt + 1}/{retries + 1})")
            last_error = "timeout"
            if attempt < retries:
                time.sleep(2)
                continue
            return None

        except requests.exceptions.HTTPError as e:
            # Check if it's a retryable 500 error
            is_500_error = e.response.status_code >= 500
            logger.error(
                f"MedGemma API HTTP error: {e.response.status_code} - {e.response.text}\n"
                f"Request payload preview: system_prompt={system_prompt[:100]}..., "
                f"user_prompt={user_prompt[:100]}... "
                f"(attempt {attempt + 1}/{retries + 1})"
            )
            last_error = f"HTTP {e.response.status_code}"

            # Retry on 500 errors (server-side bugs)
            if is_500_error and attempt < retries:
                time.sleep(3)
                continue
            return None

        except Exception as e:
            logger.error(f"MedGemma API call failed: {e} (attempt {attempt + 1}/{retries + 1})")
            last_error = str(e)
            if attempt < retries:
                time.sleep(2)
                continue
            return None

    logger.error(f"MedGemma API failed after {retries + 1} attempts. Last error: {last_error}")
    return None


def call_medgemma_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 512,
) -> dict:
    """
    Call MedGemma and parse JSON response.

    Returns:
        Parsed JSON dict or {"error": "..."} on failure
    """
    response = call_medgemma(system_prompt, user_prompt, temperature, max_tokens)

    if not response:
        return {"error": "MedGemma API call failed (no response)"}

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

        logger.error(f"MedGemma response not valid JSON: {response[:200]}")
        return {"error": "MedGemma response not valid JSON", "raw_response": response}


# Test if models endpoint is accessible
def test_connection() -> bool:
    """
    Test MedGemma API connectivity by querying /models endpoint.

    Returns:
        True if connection successful, False otherwise
    """
    try:
        models_url = f"{MEDGEMMA_BASE_URL}/models"
        response = requests.get(
            models_url,
            headers={"x-api-key": MEDGEMMA_API_KEY},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        logger.info(f"MedGemma API connected successfully. Available models: {data}")
        return True

    except Exception as e:
        logger.error(f"MedGemma API connection test failed: {e}")
        return False

