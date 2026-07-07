"""
MedGemma API Client for Medical LLM (Google's MedGemma 1.5-4B)

MedGemma is a medical-domain fine-tuned version of Gemma optimized for:
- Clinical variant interpretation
- Medical reasoning
- Healthcare NLP tasks

API: http://34.202.249.175:8080/chat
"""

import logging
import os
import json
import time
from typing import Optional
import requests

from src.utils.logging_config import get_user_friendly_logger

logger = get_user_friendly_logger('medgemma_client')

# MedGemma Configuration - load from environment or use defaults
MEDGEMMA_API_KEY = os.getenv("MEDGEMMA_API_KEY", "mgm_live_molsys")
MEDGEMMA_BASE_URL = os.getenv("MEDGEMMA_BASE_URL", "http://34.202.249.175:8080")
MEDGEMMA_ENDPOINT = f"{MEDGEMMA_BASE_URL}/chat"

# Validate API key is set
if not MEDGEMMA_API_KEY or MEDGEMMA_API_KEY == "your-medgemma-api-key-here":
    logger.warning(
        "MEDGEMMA_API_KEY not configured. Set it in .env.aws file."
    )


def call_medgemma(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    max_tokens: int = 4000,
    retries: int = 2,
) -> Optional[str]:
    """
    Call MedGemma medical-trained LLM API with retry logic.

    Args:
        system_prompt: System instructions
        user_prompt: User query
        temperature: Sampling temperature (0.0 for deterministic)
        max_tokens: Maximum response tokens
        retries: Number of retry attempts on failure (default: 2)

    Returns:
        Response text or None on error (after all retries exhausted)

    Note:
        Automatically retries on 500 errors and timeouts.
        Waits 1-2s between retries to avoid overwhelming the API.
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

    headers = {
        "x-api-key": MEDGEMMA_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
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
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()

            # Try multiple response formats for compatibility
            # Standard OpenAI format
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            # Simple response format
            elif "response" in result:
                return result["response"]
            # Direct content format
            elif "content" in result:
                return result["content"]
            # Message format
            elif "message" in result:
                if isinstance(result["message"], dict) and "content" in result["message"]:
                    return result["message"]["content"]
                return str(result["message"])
            else:
                logger.error(f"Unknown MedGemma response format: {list(result.keys())}")
                return None

        except requests.exceptions.Timeout:
            logger.error(f"MedGemma API timeout after 30s (attempt {attempt + 1}/{retries + 1})")
            last_error = "timeout"
            if attempt < retries:
                time.sleep(1)
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
                time.sleep(2)
                continue
            return None

        except Exception as e:
            logger.error(f"MedGemma API call failed: {e} (attempt {attempt + 1}/{retries + 1})")
            last_error = str(e)
            if attempt < retries:
                time.sleep(1)
                continue
            return None

    logger.error(f"MedGemma API failed after {retries + 1} attempts. Last error: {last_error}")
    return None


def call_medgemma_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    max_tokens: int = 4000,
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

