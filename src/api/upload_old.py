"""
File upload API for chat interface.
Supports VCF, PDF, CSV, and TXT files with automatic parsing and summarization.
"""

import uuid
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from src.api.db import User
from src.api.auth import verify_api_key
from src.config import OUTPUT_DIR

logger = logging.getLogger(__name__)
router = APIRouter()

# Chat storage directory
CHAT_DIR = OUTPUT_DIR / "chats"


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    """Simple file-based chat storage."""

    @staticmethod
    def load_chat(chat_id: str) -> Optional[dict]:
        chat_file = CHAT_DIR / f"{chat_id}.json"
        if not chat_file.exists():
            return None
        with open(chat_file, "r") as f:
            return json.load(f)

    @staticmethod
    def save_chat(chat_id: str, chat: dict):
        chat_file = CHAT_DIR / f"{chat_id}.json"
        with open(chat_file, "w") as f:
            json.dump(chat, f, indent=2)


@router.post("/upload")
async def upload_file_to_chat(
    chat_id: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(verify_api_key)
):
    """
    Upload a file to a chat (VCF, PDF, CSV, TXT).

    The file is:
    1. Saved to the chat directory
    2. Parsed based on file type
    3. Summarized using LLM
    4. Summary added to chat as assistant message
    """
    # Load chat
    chat = ChatStore.load_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Verify ownership (chat stores user_id, not user_email)
    if chat.get("user_id") != str(user.user_id):
        raise HTTPException(status_code=403, detail="Not your chat")

    # Create uploads directory for this chat
    upload_dir = CHAT_DIR / chat_id / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Save file
    file_id = str(uuid.uuid4())
    filename_lower = file.filename.lower()

    # Determine file extension (handle .vcf.gz)
    if filename_lower.endswith('.vcf.gz'):
        ext = '.vcf.gz'
    else:
        ext = Path(file.filename).suffix.lower()

    filepath = upload_dir / f"{file_id}{ext}"

    with open(filepath, "wb") as f:
        f.write(await file.read())

    # Parse and summarize based on file type
    summary = ""
    parsed_content = None

    try:
        if ext == ".pdf":
            from src.parsers.pdf_parser import PDFParser
            parser = PDFParser()
            parsed_content = parser.parse(str(filepath))

            # Summarize with LLM
            from src.utils.llm import call_llm
            summary = call_llm(
                system_prompt="You are a helpful assistant. Summarize documents clearly and concisely using markdown formatting.",
                user_prompt=f"Summarize this PDF document:\n\n{parsed_content[:2000]}...",
                temperature=0.7,
                max_tokens=500
            )

        elif ext in [".vcf", ".vcf.gz"]:
            # For VCF, just note that it's uploaded - actual analysis happens via /analyze
            summary = f"✅ **VCF file uploaded:** `{file.filename}`\n\nYou can now use `/analyze` to start variant classification."

        elif ext == ".txt":
            with open(filepath, "r", encoding="utf-8") as f:
                parsed_content = f.read()

            from src.utils.llm import call_llm
            summary = call_llm(
                system_prompt="You are a helpful assistant. Summarize text documents clearly using markdown.",
                user_prompt=f"Summarize this document:\n\n{parsed_content[:2000]}...",
                temperature=0.7,
                max_tokens=500
            )

        elif ext == ".csv":
            import csv
            rows = []
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)[:100]  # First 100 rows

            from src.utils.llm import call_llm
            summary = call_llm(
                system_prompt="You are a data analyst. Summarize CSV data with key statistics and trends.",
                user_prompt=f"Analyze this CSV data:\n\n{json.dumps(rows[:10], indent=2)}...\n\nTotal rows: {len(rows)}",
                temperature=0.7,
                max_tokens=500
            )

        else:
            summary = f"⚠️ File uploaded but format `{ext}` is not supported for automatic parsing. File saved as: `{file.filename}`"

        # Add messages to chat
        chat["messages"].append({
            "role": "user",
            "content": f"[Uploaded file: {file.filename}]",
            "timestamp": _now()
        })

        chat["messages"].append({
            "role": "assistant",
            "content": summary,
            "timestamp": _now()
        })

        chat["updated_at"] = _now()

        # Store file reference in chat context
        if "uploaded_files" not in chat:
            chat["uploaded_files"] = []

        chat["uploaded_files"].append({
            "file_id": file_id,
            "filename": file.filename,
            "filepath": str(filepath),
            "uploaded_at": _now()
        })

        # Update chat state based on file type and analysis mode
        if ext in [".vcf", ".vcf.gz"]:
            # Initialize context if not exists
            if "context" not in chat:
                chat["context"] = {"state": "idle", "form_data": {}}

            context = chat["context"]
            form_data = context.get("form_data", {})
            mode = form_data.get("mode", "solo")  # Default to solo if not set

            # Store VCF file path in form_data
            if mode == "solo":
                form_data["vcf_path"] = str(filepath)
                context["state"] = "solo_vcf_uploaded"
                # Add a helpful follow-up message
                chat["messages"].append({
                    "role": "assistant",
                    "content": "Which genome build was used?\n\n🔹 **GRCh38** (recommended)\n🔹 **GRCh37**\n\nType `38` or `37`.",
                    "timestamp": _now()
                })
            elif mode == "trio":
                # Handle trio VCF uploads (proband, parent1, parent2)
                if "vcf_path" not in form_data:
                    form_data["vcf_path"] = str(filepath)
                    chat["messages"].append({
                        "role": "assistant",
                        "content": "Great! Proband VCF uploaded ✅\n\nNow please upload **Father VCF** (parent 1).",
                        "timestamp": _now()
                    })
                elif "parent1_vcf" not in form_data:
                    form_data["parent1_vcf"] = str(filepath)
                    chat["messages"].append({
                        "role": "assistant",
                        "content": "Father VCF uploaded ✅\n\nFinally, please upload **Mother VCF** (parent 2).",
                        "timestamp": _now()
                    })
                elif "parent2_vcf" not in form_data:
                    form_data["parent2_vcf"] = str(filepath)
                    context["state"] = "trio_all_vcfs_uploaded"
                    chat["messages"].append({
                        "role": "assistant",
                        "content": "All VCF files uploaded ✅\n\nWhich genome build was used?\n\n🔹 **GRCh38** (recommended)\n🔹 **GRCh37**\n\nType `38` or `37`.",
                        "timestamp": _now()
                    })

            context["form_data"] = form_data

        # Save chat
        ChatStore.save_chat(chat_id, chat)

        return {
            "message": "File uploaded successfully",
            "file_id": file_id,
            "filename": file.filename,
            "summary": summary
        }

    except Exception as e:
        logger.error(f"File upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")
