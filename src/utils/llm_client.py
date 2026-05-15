"""
Reusable LLM client for all ACMG agents.
Talks to Qwen2.5-14B via vLLM on pod-b.
"""
import json
import re
import os
from openai import OpenAI
from loguru import logger
from dotenv import load_dotenv

load_dotenv("/workspace/data/acmg-pipeline/.env")

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://172.29.127.170:8000/v1")
LLM_MODEL    = os.getenv("LLM_MODEL", "qwen2.5-14b")
LLM_API_KEY  = os.getenv("LLM_API_KEY", "dummy")

client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


def call_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.1,
    retries: int = 3,
) -> str:
    """
    Send a prompt to Qwen and return the raw text response.
    Retries up to 3 times on failure.
    """
    for attempt in range(1, retries + 1):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"LLM call attempt {attempt} failed: {e}")
            if attempt == retries:
                raise
    return ""


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.1,
) -> dict:
    """
    Call LLM and parse response as JSON.
    Strips markdown fences if present.
    Returns empty dict on parse failure.
    """
    raw = call_llm(system_prompt, user_prompt, max_tokens, temperature)

    # Strip markdown code fences
    clean = re.sub(r"```json\s*", "", raw)
    clean = re.sub(r"```\s*",     "", clean).strip()

    # Extract first JSON object
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failed: {e}\nRaw: {raw[:300]}")
    return {}
