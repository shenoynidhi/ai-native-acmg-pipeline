"""
Token usage tracker for ACMG pipeline.

Tracks token usage across all LLM calls in a session for cost estimation.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class TokenTracker:
    """
    Session-level token usage tracker.

    Usage:
        tracker = TokenTracker(session_id="session_abc123")
        tracker.log_usage(input_tokens=100, output_tokens=50, model="nemotron-30b", agent="agent1")
        summary = tracker.get_summary()
    """

    def __init__(self, session_id: str, output_dir: Optional[Path] = None):
        self.session_id = session_id
        self.output_dir = output_dir or Path("/mnt/ebs-databases/output") / session_id
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.usage_file = self.output_dir / "token_usage.jsonl"
        self.total_input = 0
        self.total_output = 0
        self.by_agent: Dict[str, Dict[str, int]] = {}
        self.by_model: Dict[str, Dict[str, int]] = {}

    def log_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str = "unknown",
        agent: str = "unknown",
        operation: str = "llm_call"
    ):
        """Log token usage for a single LLM call."""
        self.total_input += input_tokens
        self.total_output += output_tokens

        # Track by agent
        if agent not in self.by_agent:
            self.by_agent[agent] = {"input": 0, "output": 0, "calls": 0}
        self.by_agent[agent]["input"] += input_tokens
        self.by_agent[agent]["output"] += output_tokens
        self.by_agent[agent]["calls"] += 1

        # Track by model
        if model not in self.by_model:
            self.by_model[model] = {"input": 0, "output": 0, "calls": 0}
        self.by_model[model]["input"] += input_tokens
        self.by_model[model]["output"] += output_tokens
        self.by_model[model]["calls"] += 1

        # Append to JSONL file for audit trail
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "session_id": self.session_id,
            "agent": agent,
            "model": model,
            "operation": operation,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

        with open(self.usage_file, "a") as f:
            f.write(json.dumps(record) + "\n")

    def get_summary(self) -> Dict:
        """Get summary of token usage for this session."""
        return {
            "session_id": self.session_id,
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "total_tokens": self.total_input + self.total_output,
            "by_agent": self.by_agent,
            "by_model": self.by_model,
            "usage_file": str(self.usage_file),
        }

    def save_summary(self):
        """Save final summary to JSON file."""
        summary_file = self.output_dir / "token_usage_summary.json"
        with open(summary_file, "w") as f:
            json.dump(self.get_summary(), f, indent=2)
        logger.info(f"[{self.session_id}] Token usage summary saved to {summary_file}")
        return summary_file


# Global tracker registry (session_id -> TokenTracker)
_trackers: Dict[str, TokenTracker] = {}


def get_tracker(session_id: str) -> TokenTracker:
    """Get or create tracker for a session."""
    if session_id not in _trackers:
        _trackers[session_id] = TokenTracker(session_id)
    return _trackers[session_id]


def log_tokens(
    session_id: str,
    input_tokens: int,
    output_tokens: int,
    model: str = "unknown",
    agent: str = "unknown",
    operation: str = "llm_call"
):
    """Convenience function to log token usage."""
    tracker = get_tracker(session_id)
    tracker.log_usage(input_tokens, output_tokens, model, agent, operation)


def get_session_summary(session_id: str) -> Dict:
    """Get summary for a session."""
    if session_id in _trackers:
        return _trackers[session_id].get_summary()

    # Try to load from file if tracker doesn't exist
    summary_file = Path("/mnt/ebs-databases/output") / session_id / "token_usage_summary.json"
    if summary_file.exists():
        with open(summary_file) as f:
            return json.load(f)

    return {"error": f"No usage data found for session {session_id}"}


def finalize_session(session_id: str):
    """Finalize and save summary for a session."""
    if session_id in _trackers:
        tracker = _trackers[session_id]
        tracker.save_summary()
        logger.info(
            f"[{session_id}] Total tokens used: {tracker.total_input + tracker.total_output} "
            f"(input: {tracker.total_input}, output: {tracker.total_output})"
        )
        return tracker.get_summary()
    return None

