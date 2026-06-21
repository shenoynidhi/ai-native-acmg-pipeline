"""
src/api/chat.py

Chat interface API routes - integrated with existing ACMG pipeline.
Conversational UI for variant analysis submission (solo and trio modes).
"""

import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.db import get_db, User, Session as DBSession
from src.api.auth import verify_api_key
from src.config import OUTPUT_DIR

logger = logging.getLogger(__name__)

router = APIRouter()

# Chat storage directory
CHAT_DIR = OUTPUT_DIR / "chats"
CHAT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class NewChatRequest(BaseModel):
    title: str = "New Chat"

class RenameChatRequest(BaseModel):
    title: str

class SendMessageRequest(BaseModel):
    chat_id: str
    content: str

class UploadFileRequest(BaseModel):
    chat_id: str


# ---------------------------------------------------------------------------
# Chat Storage
# ---------------------------------------------------------------------------

class ChatStore:
    """File-based chat storage (can be migrated to DB later)."""

    @staticmethod
    def _get_chat_path(chat_id: str) -> Path:
        return CHAT_DIR / f"{chat_id}.json"

    @staticmethod
    def get_chat(chat_id: str, user_id: str) -> Optional[Dict]:
        """Load chat from disk."""
        path = ChatStore._get_chat_path(chat_id)
        if not path.exists():
            return None

        try:
            with open(path, 'r') as f:
                chat = json.load(f)
                # Security: verify chat belongs to user
                if chat.get("user_id") != str(user_id):
                    return None
                return chat
        except Exception as e:
            logger.error(f"Failed to load chat {chat_id}: {e}")
            return None

    @staticmethod
    def save_chat(chat_id: str, chat: Dict):
        """Save chat to disk."""
        path = ChatStore._get_chat_path(chat_id)
        try:
            with open(path, 'w') as f:
                json.dump(chat, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save chat {chat_id}: {e}")
            raise

    @staticmethod
    def list_chats(user_id: str) -> List[Dict]:
        """List all chats for a user."""
        chats = []
        for path in CHAT_DIR.glob("*.json"):
            try:
                with open(path, 'r') as f:
                    chat = json.load(f)
                    if chat.get("user_id") == str(user_id):
                        # Return summary only (exclude messages)
                        chat_id = chat.get("chat_id") or chat.get("id")
                        chats.append({
                            "chat_id": chat_id,
                            "id": chat_id,
                            "title": chat["title"],
                            "created_at": chat["created_at"],
                            "updated_at": chat["updated_at"],
                            "message_count": len(chat.get("messages", [])),
                        })
            except Exception as e:
                logger.warning(f"Failed to load chat {path.name}: {e}")
                continue

        # Sort by updated_at descending
        chats.sort(key=lambda c: c["updated_at"], reverse=True)
        return chats

    @staticmethod
    def delete_chat(chat_id: str, user_id: str) -> bool:
        """Delete chat from disk."""
        # Verify ownership first
        chat = ChatStore.get_chat(chat_id, user_id)
        if not chat:
            return False

        path = ChatStore._get_chat_path(chat_id)
        try:
            path.unlink()
            return True
        except Exception as e:
            logger.error(f"Failed to delete chat {chat_id}: {e}")
            return False


# ---------------------------------------------------------------------------
# LLM Client (uses AWS Bedrock)
# ---------------------------------------------------------------------------

def _call_llm(messages: List[Dict], system_prompt: str) -> str:
    """
    Call LLM for conversational responses using Bedrock.

    Uses the unified LLM client (automatically routes to Bedrock or vLLM).
    """
    try:
        from src.utils.llm import call_llm

        # Extract last user message for prompt
        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        user_prompt = user_messages[-1] if user_messages else ""

        # Call LLM
        response = call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.7,
            max_tokens=500,  # Keep responses concise
            retries=2
        )

        return response

    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return "I'm having trouble connecting to the AI service. Please try again in a moment."


# ---------------------------------------------------------------------------
# Chat Routes
# ---------------------------------------------------------------------------

@router.post("/new")
def create_chat(
    req: NewChatRequest,
    user: User = Depends(verify_api_key)
):
    """Create a new chat session."""
    chat_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    chat = {
        "chat_id": chat_id,
        "id": chat_id,
        "user_id": str(user.user_id),
        "title": req.title,
        "created_at": now,
        "updated_at": now,
        "messages": [
            {
                "role": "assistant",
                "content": """👋 **Hi! I'm your ACMG variant analysis assistant.**

I can help you submit variant analyses and track your results. Here's what I can do:

🧬 **Solo Analysis** — Analyze a single patient VCF
👨‍👩‍👦 **Trio Analysis** — Analyze proband + parental VCFs (enables de novo & segregation detection)
📊 **Dashboard** — View your past analyses
❓ **Help** — See all available commands

**Quick start:** Type `/analyze` to begin, or `/help` for all commands.

What would you like to do?""",
                "timestamp": now,
                "type": "welcome"
            }
        ],
        "context": {
            "state": "idle",  # idle, registration, solo_form, trio_form, analyzing
            "form_data": {},  # Stores form fields as user provides them
        }
    }

    ChatStore.save_chat(chat_id, chat)

    return chat


@router.get("/")
def list_chats(user: User = Depends(verify_api_key)):
    """List all chats for authenticated user."""
    return {"chats": ChatStore.list_chats(str(user.user_id))}


@router.get("/{chat_id}")
def get_chat(
    chat_id: str,
    user: User = Depends(verify_api_key)
):
    """Get chat by ID."""
    chat = ChatStore.get_chat(chat_id, str(user.user_id))
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.post("/send")
def send_message(
    req: SendMessageRequest,
    user: User = Depends(verify_api_key)
):
    """
    Send a message in a chat.

    Handles conversational form filling for solo and trio analysis submission.
    """
    chat = ChatStore.get_chat(req.chat_id, str(user.user_id))
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    now = datetime.now(timezone.utc).isoformat()
    user_msg = {
        "role": "user",
        "content": req.content,
        "timestamp": now,
    }
    chat["messages"].append(user_msg)

    # Auto-title on first user message
    if len(chat["messages"]) <= 3:
        chat["title"] = req.content[:50].strip() + ("..." if len(req.content) > 50 else "")

    # Process user input based on chat state
    context = chat.get("context", {})
    state = context.get("state", "idle")
    form_data = context.get("form_data", {})

    response_text = _process_user_input(
        user_input=req.content,
        state=state,
        form_data=form_data,
        chat_history=chat["messages"],
        user=user
    )

    # Add assistant response
    assistant_msg = {
        "role": "assistant",
        "content": response_text,
        "timestamp": now,
    }
    chat["messages"].append(assistant_msg)
    chat["updated_at"] = now

    # Save updated chat
    ChatStore.save_chat(req.chat_id, chat)

    return assistant_msg


def _process_user_input(
    user_input: str,
    state: str,
    form_data: Dict,
    chat_history: List[Dict],
    user: User
) -> str:
    """
    Process user input based on current chat state.

    State machine for conversational form filling.
    """
    user_lower = user_input.strip().lower()

    # Command handling
    if user_lower in ["/help", "help"]:
        return _help_message()

    if user_lower in ["/analyze", "analyze"]:
        form_data.clear()
        return "Sure! What type of analysis?\n\n1️⃣ **Solo** — Single patient VCF\n2️⃣ **Trio** — Proband + Mother + Father VCFs\n\nType `1` for Solo or `2` for Trio."

    if user_lower.startswith("/status"):
        # Check if session_id provided
        parts = user_input.strip().split()
        if len(parts) >= 2:
            session_id = parts[1]
            return _get_detailed_status(session_id, user)
        else:
            return _get_status_summary(user)

    if user_lower in ["/history", "history"]:
        return _get_history_summary(user)

    # Form filling logic
    if state == "idle":
        # User selected analysis type
        if user_lower in ["solo", "1", "solo analysis"]:
            form_data["mode"] = "solo"
            return "Perfect! 🧬 **Solo Analysis Mode**\n\nPlease upload your proband VCF file using the upload button."

        elif user_lower in ["trio", "2", "trio analysis"]:
            form_data["mode"] = "trio"
            return "Great! 👨‍👩‍👦 **Trio Analysis Mode**\n\nTrio mode enables:\n✅ De novo variant detection (PS2/PM6)\n✅ Segregation analysis (PP1/BS2/BS4)\n✅ Compound het detection (PM3)\n\nPlease upload **proband VCF** first."

        else:
            # Use LLM for general conversation
            return _call_llm(chat_history, _get_system_prompt())

    elif state == "solo_vcf_uploaded" or state == "trio_all_vcfs_uploaded":
        # Ask for genome build
        if "genome_build" not in form_data:
            # Check if user provided it
            if "grch38" in user_lower or "hg38" in user_lower or "38" in user_lower:
                form_data["genome_build"] = "GRCh38"
                return "Got it! Genome build: **GRCh38** ✅\n\nNext, what is the patient's sex?\n\n🔹 Male\n🔹 Female\n🔹 Unknown"

            elif "grch37" in user_lower or "hg19" in user_lower or "37" in user_lower:
                form_data["genome_build"] = "GRCh37"
                return "Got it! Genome build: **GRCh37** ✅\n\nNext, what is the patient's sex?\n\n🔹 Male\n🔹 Female\n🔹 Unknown"

            else:
                return "Which genome build was used?\n\n🔹 **GRCh38** (recommended)\n🔹 **GRCh37**\n\nType `38` or `37`."

        # Ask for patient sex
        elif "proband_sex" not in form_data:
            if "male" in user_lower or "m" == user_lower:
                form_data["proband_sex"] = "male"
                return "Got it! Patient sex: **Male** ✅\n\nPlease provide clinical notes (symptoms, family history, etc.).\n\nOr type `skip` if none."

            elif "female" in user_lower or "f" == user_lower:
                form_data["proband_sex"] = "female"
                return "Got it! Patient sex: **Female** ✅\n\nPlease provide clinical notes (symptoms, family history, etc.).\n\nOr type `skip` if none."

            elif "unknown" in user_lower or "u" == user_lower or "skip" in user_lower:
                form_data["proband_sex"] = "unknown"
                return "Got it! Patient sex: **Unknown** ✅\n\nPlease provide clinical notes (symptoms, family history, etc.).\n\nOr type `skip` if none."

            else:
                return "What is the patient's sex?\n\n🔹 Male\n🔹 Female\n🔹 Unknown\n\nType `M`, `F`, or `U`."

        # Ask for clinical notes
        elif "clinical_notes" not in form_data:
            if user_lower == "skip":
                form_data["clinical_notes"] = ""
            else:
                form_data["clinical_notes"] = user_input

            return "Got it! 📝\n\nFinally, do you have **HPO terms** for this patient? (Optional)\n\nExample: `HP:0001250, HP:0001263`\n\nOr type `skip`."

        # Ask for HPO terms
        elif "hpo_terms" not in form_data:
            if user_lower == "skip":
                form_data["hpo_terms"] = ""
            else:
                form_data["hpo_terms"] = user_input

            # ALL FIELDS COLLECTED — Submit analysis!
            return _submit_analysis(form_data, user)

    else:
        # Default: LLM conversation
        return _call_llm(chat_history, _get_system_prompt())


def _submit_analysis(form_data: Dict, user: User) -> str:
    """
    Submit analysis to the existing ACMG pipeline.

    This calls your real pipeline (POST /analyze) instead of mock data.
    """
    try:
        # Build parameters for your existing API
        from src.api.worker import submit_analysis
        from src.api.db import get_db

        session_id = f"session_{uuid.uuid4().hex[:12]}"

        # Get VCF paths from form_data (uploaded earlier via /upload)
        vcf_path = form_data.get("vcf_path")  # Fixed: use "vcf_path" not "proband_vcf_path"
        if not vcf_path:
            return "❌ Error: VCF file not found. Please upload again."

        params = {
            "genome_build": form_data.get("genome_build", "GRCh38"),
            "clinical_notes": form_data.get("clinical_notes", ""),
            "proband_sex": form_data.get("proband_sex", "unknown"),
            "patient_hpo_terms": [t.strip() for t in form_data.get("hpo_terms", "").split(",") if t.strip()],
        }

        # Trio mode parameters
        if form_data.get("mode") == "trio":
            params["parent1_vcf_path"] = form_data.get("parent1_vcf")  # Fixed key name
            params["parent2_vcf_path"] = form_data.get("parent2_vcf")  # Fixed key name
            params["proband_bam_path"] = form_data.get("proband_bam_path")
            params["parent1_bam_path"] = form_data.get("parent1_bam_path")
            params["parent2_bam_path"] = form_data.get("parent2_bam_path")

        # Create database session record BEFORE submitting to Celery
        db = next(get_db())
        try:
            mode = form_data.get("mode", "solo")
            db_session = DBSession(
                session_id=session_id,
                user_id=user.user_id,
                vcf_path=vcf_path,
                vcf_filename=Path(vcf_path).name if vcf_path else None,
                genome_build=params["genome_build"],
                analysis_mode=mode,
                trio_mode=(mode == "trio"),
                clinical_notes=params.get("clinical_notes", ""),
                proband_sex=params.get("proband_sex", "unknown"),
                hpo_terms=params.get("patient_hpo_terms", []),
                status="queued",
                progress_pct=0,
                current_step="Queued for processing..."
            )
            db.add(db_session)
            db.commit()
        finally:
            db.close()

        # Submit to Celery worker (your existing pipeline)
        task_id = submit_analysis(
            session_id=session_id,
            vcf_path=vcf_path,
            params=params
        )

        mode_label = "Trio" if form_data.get("mode") == "trio" else "Solo"

        return f"""✅ **Analysis submitted successfully!**

**Mode:** {mode_label}
**Session ID:** `{session_id}`
**Genome Build:** {params['genome_build']}

Your analysis is now queued. Use the commands below to track progress:

📊 `/status {session_id}` - Check current status
🔍 View results at: [Dashboard](/analysis/{session_id})

I'll check back and notify you when reports are ready for download! 🎉"""

    except Exception as e:
        logger.error(f"Analysis submission failed: {e}", exc_info=True)
        return f"❌ **Submission failed:** {str(e)}\n\nPlease try again or contact support."


@router.delete("/{chat_id}")
def delete_chat(
    chat_id: str,
    user: User = Depends(verify_api_key)
):
    """Delete a chat."""
    success = ChatStore.delete_chat(chat_id, str(user.user_id))
    if not success:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"ok": True}


@router.put("/{chat_id}/rename")
def rename_chat(
    chat_id: str,
    req: RenameChatRequest,
    user: User = Depends(verify_api_key)
):
    """Rename a chat."""
    chat = ChatStore.get_chat(chat_id, str(user.user_id))
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    chat["title"] = req.title
    chat["updated_at"] = datetime.now(timezone.utc).isoformat()
    ChatStore.save_chat(chat_id, chat)

    return {"ok": True}


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _get_system_prompt() -> str:
    """System prompt for LLM conversations."""
    return """You are Molsys AI, an expert ACMG variant analysis assistant.

You help users submit variant analyses in a conversational way.

Rules:
1. ALWAYS respond directly to the user. Do NOT write internal monologues.
2. NEVER ask for more than one piece of information at a time.
3. Keep responses to 1-2 sentences maximum.
4. Be friendly and supportive.

When users ask questions about variant classification, ACMG criteria, or genomics, answer helpfully and concisely."""


def _help_message() -> str:
    """Help message with available commands."""
    return """🌟 **Available Commands:**

**Analysis:**
- `/analyze` — Start a new variant analysis
- `/status [session_id]` — Check analysis status
- `/history` — View past analyses

**Chat:**
- `/help` — Show this help message
- `/clear` — Clear current chat

**Info:**
- Type naturally to ask questions about ACMG classification, variants, or genomics!

What would you like to do?"""


def _get_detailed_status(session_id: str, user: User) -> str:
    """Get detailed status of a specific analysis with download links."""
    from src.api.db import SessionLocal
    import os

    db = SessionLocal()
    try:
        session = db.query(DBSession).filter(
            DBSession.session_id == session_id,
            DBSession.user_id == user.user_id
        ).first()

        if not session:
            return f"❌ **Session not found:** `{session_id}`\n\nCheck your session ID or use `/status` to see all analyses."

        status_emoji = {
            "queued": "⏳",
            "running": "▶️",
            "complete": "✅",
            "failed": "❌"
        }.get(session.status, "❓")

        lines = [f"{status_emoji} **Analysis Status: {session.status.upper()}**\n"]
        lines.append(f"**Session ID:** `{session_id}`")
        lines.append(f"**Progress:** {session.progress_pct or 0}%")

        if session.current_step:
            lines.append(f"**Current Step:** {session.current_step}")

        if session.variant_count:
            lines.append(f"**Variants:** {session.variant_count}")

        # Show download links if complete
        if session.status == "complete" and session.report_paths:
            lines.append("\n📥 **Download Reports:**\n")

            api_base = os.getenv("API_BASE_URL", "http://localhost:8000")

            if session.report_paths.get("xlsx"):
                lines.append(f"📊 [Excel Report]({api_base}/download/{session_id}/xlsx)")

            if session.report_paths.get("tsv"):
                lines.append(f"📄 [TSV Report]({api_base}/download/{session_id}/tsv)")

            if session.report_paths.get("html"):
                lines.append(f"🌐 [HTML Report]({api_base}/download/{session_id}/html)")

            lines.append(f"\n🔍 [View Full Results]({api_base.replace(':8000', ':3000')}/analysis/{session_id})")

        elif session.status == "failed":
            lines.append(f"\n❌ **Error:** {session.error or 'Unknown error'}")

        elif session.status in ["queued", "running"]:
            lines.append("\n⏳ Analysis in progress... Check back soon!")

        return "\n".join(lines)

    finally:
        db.close()


def _get_status_summary(user: User) -> str:
    """Get summary of user's recent analyses."""
    from src.api.db import SessionLocal

    db = SessionLocal()
    try:
        sessions = db.query(DBSession).filter(
            DBSession.user_id == user.user_id
        ).order_by(DBSession.created_at.desc()).limit(5).all()

        if not sessions:
            return "📊 **No analyses found.**\n\nType `/analyze` to start your first analysis!"

        lines = ["📊 **Recent Analyses:**\n"]
        for s in sessions:
            status_emoji = {
                "queued": "⏳",
                "running": "▶️",
                "complete": "✅",
                "failed": "❌"
            }.get(s.status, "❓")

            lines.append(f"{status_emoji} `{s.session_id}` — {s.status} ({s.progress_pct or 0}%)")

        lines.append("\n💡 **Tip:** Use `/status <session_id>` for detailed progress and download links.")
        return "\n".join(lines)

    finally:
        db.close()


def _get_history_summary(user: User) -> str:
    """Get formatted history of completed analyses."""
    from src.api.db import SessionLocal

    db = SessionLocal()
    try:
        sessions = db.query(DBSession).filter(
            DBSession.user_id == user.user_id,
            DBSession.status == "complete"
        ).order_by(DBSession.completed_at.desc()).limit(10).all()

        if not sessions:
            return "📜 **No completed analyses yet.**\n\nYour first completed analysis will appear here!"

        lines = ["📜 **Analysis History:**\n"]
        for s in sessions:
            date = s.completed_at.strftime("%Y-%m-%d %H:%M") if s.completed_at else "N/A"
            variant_info = f"{s.variant_count} variants" if s.variant_count else "N/A"
            lines.append(f"✅ `{s.session_id}` — {variant_info} ({date})")

        lines.append("\n💡 **Download reports:** `/download/<session_id>/xlsx`")
        return "\n".join(lines)

    finally:
        db.close()
