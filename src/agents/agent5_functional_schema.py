"""
JSON Schema for Agent 5 (Functional) - Strict Output Enforcement

Use this schema with bedrock_client_converse to force valid JSON output.

Usage:
    from src.agents.agent5_functional_schema import AGENT5_SCHEMA

    result = call_llm_json(
        system_prompt,
        user_prompt,
        reasoning_effort="high",
        json_schema=AGENT5_SCHEMA  # Enforce schema
    )
"""

AGENT5_SCHEMA = {
    "strict": True,
    "name": "agent5_functional_output",
    "description": "Agent 5 functional evidence assessment output",
    "schema": {
        "type": "object",
        "properties": {
            "criteria_pathogenic": {
                "type": "object",
                "description": "Pathogenic criteria applied (PS3, PM1)",
                "properties": {
                    "PS3": {
                        "type": "string",
                        "enum": [
                            "Very_Strong",
                            "Strong",
                            "Moderate",
                            "Supporting",
                            "Not_Met",
                            "Not_Applicable"
                        ],
                        "description": "Well-established functional studies show damaging effect"
                    },
                    "PM1": {
                        "type": "string",
                        "enum": [
                            "Moderate",
                            "Supporting",
                            "Not_Met",
                            "Not_Applicable"
                        ],
                        "description": "Located in mutational hot spot or critical functional domain"
                    }
                },
                "additionalProperties": False
            },
            "criteria_benign": {
                "type": "object",
                "description": "Benign criteria applied (BS3)",
                "properties": {
                    "BS3": {
                        "type": "string",
                        "enum": [
                            "Strong",
                            "Supporting",
                            "Not_Met",
                            "Not_Applicable"
                        ],
                        "description": "Well-established functional studies show no damaging effect"
                    }
                },
                "additionalProperties": False
            },
            "confidence": {
                "type": "string",
                "enum": ["HIGH", "MEDIUM", "LOW"],
                "description": "Confidence in the assessment"
            },
            "notes": {
                "type": "string",
                "description": "Evidence notes and reasoning"
            }
        },
        "required": ["criteria_pathogenic", "criteria_benign", "confidence"],
        "additionalProperties": False
    }
}


# Example usage function (optional)
def validate_agent5_output(output: dict) -> bool:
    """
    Validate that Agent 5 output matches the schema.

    Returns:
        True if valid, False otherwise
    """
    if not isinstance(output, dict):
        return False

    required_keys = {"criteria_pathogenic", "criteria_benign", "confidence"}
    if not required_keys.issubset(output.keys()):
        return False

    # Validate confidence
    if output.get("confidence") not in {"HIGH", "MEDIUM", "LOW"}:
        return False

    # Validate criteria are dicts
    if not isinstance(output.get("criteria_pathogenic"), dict):
        return False
    if not isinstance(output.get("criteria_benign"), dict):
        return False

    return True

