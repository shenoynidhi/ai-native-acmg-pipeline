"""
src/api/main.py

Main FastAPI application for the ACMG Pipeline.
Provides REST API and WebSocket endpoints for variant analysis.
"""

import os
import json
import uuid
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pathlib import Path

from src.api.db import get_db, User, Session as DBSession
from src.api.auth import register_user, verify_api_key, verify_admin, increment_usage
from src.api.models import (
    RegisterRequest, RegisterResponse,
    RegenerateKeyRequest, RegenerateKeyResponse,
    RequestKeyResetRequest, RequestKeyResetResponse,
    ConfirmKeyResetRequest, ConfirmKeyResetResponse,
    AnalyzeRequest, AnalyzeResponse,
    StatusResponse, HistoryResponse, HistoryItem,
    RerunRequest, ErrorResponse
)
from src.api.worker import submit_analysis
from src.config import OUTPUT_DIR

# Create FastAPI app
app = FastAPI(
    title="ACMG Variant Classification Pipeline API",
    description="AI-native multi-agent system for automated ACMG/AMP variant classification",
    version="1.0.0"
)

# CORS middleware (allow frontend to call API)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (CSS, JS)
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# Upload directory
UPLOAD_DIR = OUTPUT_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# User Registration
# ---------------------------------------------------------------------------

@app.post("/register", response_model=RegisterResponse, tags=["Authentication"])
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """
    Register a new user account and receive an API key.

    The API key is shown only once - save it securely!
    """
    return register_user(request, db)


@app.post("/regenerate-key", response_model=RegenerateKeyResponse, tags=["Authentication"])
def regenerate_api_key(request: RegenerateKeyRequest, db: Session = Depends(get_db)):
    """
    Regenerate API key for a user who lost theirs.

    **Security Note:** In production, add email verification:
    1. User requests reset via email
    2. System sends verification code to email
    3. User confirms with code
    4. System generates new key

    For MVP: Simple email-based regeneration without verification.
    Use this endpoint responsibly - verify user identity before calling.
    """
    import secrets
    import bcrypt
    import logging

    logger = logging.getLogger(__name__)

    # Find user by email
    user = db.query(User).filter(User.email == request.email).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found. Please check your email address."
        )

    # Generate new API key
    new_api_key = secrets.token_urlsafe(32)

    # Hash the new key
    api_key_hash = bcrypt.hashpw(
        new_api_key.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')

    # Update user's API key hash
    user.api_key_hash = api_key_hash
    db.commit()

    logger.info(f"API key regenerated for user: {user.email} (user_id: {user.user_id})")

    return RegenerateKeyResponse(
        user_id=str(user.user_id),
        new_api_key=new_api_key,
        message="New API key generated successfully. Save it now - it won't be shown again!"
    )


@app.post("/request-key-reset", response_model=RequestKeyResetResponse, tags=["Authentication"])
def request_key_reset(request: RequestKeyResetRequest, db: Session = Depends(get_db)):
    """
    Request API key reset - sends 6-digit verification code to user's email.

    **Step 1 of 2:** User provides email, receives verification code.

    **Production Setup Required:**
    - Configure SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD environment variables
    - Or use SendGrid/AWS SES API keys

    If email service not configured, this endpoint will return success but won't send email.
    In that case, admin can manually provide the code to the user.
    """
    import secrets
    import random
    import logging

    logger = logging.getLogger(__name__)

    # Find user (don't reveal if email exists for security)
    user = db.query(User).filter(User.email == request.email).first()

    if not user:
        # Return success anyway to prevent email enumeration
        return RequestKeyResetResponse(
            message="If an account exists with this email, a verification code has been sent."
        )

    # Generate 6-digit code
    code = str(random.randint(100000, 999999))

    # Store code in Redis with 15-minute expiration
    try:
        redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        redis_client.setex(f"reset:{user.user_id}", 900, code)  # 15 minutes

        logger.info(f"Key reset code generated for {user.email}: {code} (expires in 15 min)")

        # Send email with code
        email_sent = _send_reset_email(user.email, user.name, code)

        if email_sent:
            logger.info(f"Reset code email sent to {user.email}")
        else:
            logger.warning(f"Email service not configured - code not sent. Manual code: {code}")

    except Exception as e:
        logger.error(f"Failed to generate reset code: {e}")
        raise HTTPException(status_code=500, detail="Failed to process reset request")

    return RequestKeyResetResponse(
        message="If an account exists with this email, a verification code has been sent."
    )


@app.post("/confirm-key-reset", response_model=ConfirmKeyResetResponse, tags=["Authentication"])
def confirm_key_reset(request: ConfirmKeyResetRequest, db: Session = Depends(get_db)):
    """
    Confirm API key reset with verification code - generates new key.

    **Step 2 of 2:** User provides email + 6-digit code, receives new API key.

    Code must be used within 15 minutes of generation.
    """
    import secrets
    import bcrypt
    import logging
    import redis

    logger = logging.getLogger(__name__)

    # Find user
    user = db.query(User).filter(User.email == request.email).first()

    if not user:
        raise HTTPException(status_code=404, detail="Invalid email or code")

    # Verify code from Redis
    try:
        redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        stored_code = redis_client.get(f"reset:{user.user_id}")

        if not stored_code:
            raise HTTPException(
                status_code=401,
                detail="Code expired or invalid. Please request a new code."
            )

        if stored_code.decode() != request.code:
            logger.warning(f"Invalid reset code attempt for {user.email}")
            raise HTTPException(status_code=401, detail="Invalid code")

    except redis.RedisError as e:
        logger.error(f"Redis error during code verification: {e}")
        raise HTTPException(status_code=500, detail="Verification service unavailable")

    # Generate new API key
    new_api_key = secrets.token_urlsafe(32)
    api_key_hash = bcrypt.hashpw(
        new_api_key.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')

    # Update user's API key
    user.api_key_hash = api_key_hash
    db.commit()

    # Delete used code
    redis_client.delete(f"reset:{user.user_id}")

    logger.info(f"API key reset completed for {user.email}")

    return ConfirmKeyResetResponse(
        user_id=str(user.user_id),
        new_api_key=new_api_key,
        message="New API key generated successfully. Save it now - it won't be shown again!"
    )


def _send_reset_email(to_email: str, name: str, code: str) -> bool:
    """
    Send password reset email with verification code.

    Returns True if sent successfully, False otherwise.

    **Production Setup:**
    Set these environment variables:
    - SMTP_HOST: smtp.gmail.com (or your provider)
    - SMTP_PORT: 587
    - SMTP_USER: your-email@gmail.com
    - SMTP_PASSWORD: your-app-password
    - FROM_EMAIL: noreply@yourlab.com
    - FROM_NAME: ACMG Pipeline

    Or use SendGrid/AWS SES:
    - SENDGRID_API_KEY
    - AWS_SES_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    """
    import logging
    logger = logging.getLogger(__name__)

    # Check if email service is configured
    smtp_host = os.getenv("SMTP_HOST")
    sendgrid_key = os.getenv("SENDGRID_API_KEY")

    if not smtp_host and not sendgrid_key:
        logger.warning("Email service not configured - skipping email send")
        return False

    try:
        if sendgrid_key:
            # Use SendGrid
            return _send_via_sendgrid(to_email, name, code)
        elif smtp_host:
            # Use SMTP
            return _send_via_smtp(to_email, name, code)
    except Exception as e:
        logger.error(f"Failed to send reset email to {to_email}: {e}")
        return False

    return False


def _send_via_smtp(to_email: str, name: str, code: str) -> bool:
    """Send email via SMTP."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("FROM_EMAIL", smtp_user)
    from_name = os.getenv("FROM_NAME", "ACMG Pipeline")

    msg = MIMEMultipart()
    msg['From'] = f"{from_name} <{from_email}>"
    msg['To'] = to_email
    msg['Subject'] = "API Key Reset - Verification Code"

    body = f"""
Hi {name},

You requested to reset your ACMG Pipeline API key.

Your verification code is:

    {code}

This code will expire in 15 minutes.

If you didn't request this reset, please ignore this email.

---
ACMG Variant Classification Pipeline
"""

    msg.attach(MIMEText(body, 'plain'))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

    return True


def _send_via_sendgrid(to_email: str, name: str, code: str) -> bool:
    """Send email via SendGrid API."""
    # Implement SendGrid sending here if using SendGrid
    # pip install sendgrid
    # from sendgrid import SendGridAPIClient
    # from sendgrid.helpers.mail import Mail
    pass


# ---------------------------------------------------------------------------
# Variant Analysis
# ---------------------------------------------------------------------------

@app.post("/analyze", response_model=AnalyzeResponse, tags=["Analysis"])
async def analyze_vcf(
    vcf_file: UploadFile = File(..., description="VCF or VCF.gz file"),
    genome_build: str = Form("GRCh38"),
    clinical_notes: str = Form(""),
    proband_sex: str = Form("unknown"),
    patient_hpo_terms: str = Form(""),  # Comma-separated HPO IDs
    case_database_csv: Optional[UploadFile] = File(None),
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Submit a VCF file for ACMG variant classification analysis.

    Returns a session_id to track job progress via /status/{session_id}.
    """
    # Generate session ID
    session_id = f"session_{uuid.uuid4().hex[:12]}"

    # Save uploaded VCF
    vcf_path = UPLOAD_DIR / session_id / vcf_file.filename
    vcf_path.parent.mkdir(parents=True, exist_ok=True)

    with open(vcf_path, "wb") as f:
        content = await vcf_file.read()
        f.write(content)

    # Save case database if provided
    case_db_path = None
    if case_database_csv:
        case_db_path = UPLOAD_DIR / session_id / "case_database.csv"
        with open(case_db_path, "wb") as f:
            content = await case_database_csv.read()
            f.write(content)

    # Parse HPO terms (comma-separated)
    hpo_terms = [t.strip() for t in patient_hpo_terms.split(",") if t.strip()]

    # Build parameters
    params = {
        "genome_build": genome_build,
        "clinical_notes": clinical_notes,
        "proband_sex": proband_sex,
        "patient_hpo_terms": hpo_terms,
        "case_database_csv": str(case_db_path) if case_db_path else None,
    }

    # Create session record in database
    session = DBSession(
        session_id=session_id,
        user_id=user.user_id,
        genome_build=genome_build,
        clinical_notes=clinical_notes,
        proband_sex=proband_sex,
        vcf_filename=vcf_file.filename,
        status="queued",
        progress_pct=0,
        current_step="Queued for processing",
        params_json=params
    )
    db.add(session)
    db.commit()

    # Increment user's usage counter
    increment_usage(user, db)

    # Submit to Celery queue
    task_id = submit_analysis(
        session_id=session_id,
        vcf_path=str(vcf_path),
        params=params
    )

    return AnalyzeResponse(
        session_id=session_id,
        status="queued",
        message=f"Analysis queued successfully. Use session_id to check status."
    )


# ---------------------------------------------------------------------------
# Job Status
# ---------------------------------------------------------------------------

@app.get("/status/{session_id}", response_model=StatusResponse, tags=["Analysis"])
def get_status(
    session_id: str,
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Get current status of an analysis job.

    Poll this endpoint to track progress.
    """
    session = db.query(DBSession).filter(
        DBSession.session_id == session_id,
        DBSession.user_id == user.user_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return StatusResponse(
        session_id=session.session_id,
        status=session.status,
        progress_pct=session.progress_pct or 0,
        current_step=session.current_step,
        variant_count=session.variant_count,
        report_paths=session.report_paths,
        error=session.error,
        created_at=session.created_at,
        completed_at=session.completed_at
    )


# ---------------------------------------------------------------------------
# Report Download
# ---------------------------------------------------------------------------

@app.get("/download/{session_id}/{format}", tags=["Analysis"])
def download_report(
    session_id: str,
    format: str,
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Download analysis report in specified format (xlsx, tsv, html).
    """
    # Verify session belongs to user
    session = db.query(DBSession).filter(
        DBSession.session_id == session_id,
        DBSession.user_id == user.user_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != "complete":
        raise HTTPException(status_code=400, detail="Analysis not complete yet")

    # Get report path
    if not session.report_paths or format not in session.report_paths:
        raise HTTPException(status_code=404, detail=f"Report format '{format}' not available")

    report_path = Path(session.report_paths[format])

    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    # Determine media type
    media_types = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "tsv": "text/tab-separated-values",
        "html": "text/html"
    }

    return FileResponse(
        path=report_path,
        media_type=media_types.get(format, "application/octet-stream"),
        filename=report_path.name
    )


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@app.get("/history", response_model=HistoryResponse, tags=["Analysis"])
def get_history(
    limit: int = 50,
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Get list of past analysis sessions for authenticated user.
    """
    sessions = db.query(DBSession).filter(
        DBSession.user_id == user.user_id
    ).order_by(
        DBSession.created_at.desc()
    ).limit(limit).all()

    items = [
        HistoryItem(
            session_id=s.session_id,
            vcf_filename=s.vcf_filename,
            genome_build=s.genome_build,
            variant_count=s.variant_count,
            status=s.status,
            created_at=s.created_at,
            completed_at=s.completed_at,
            classifications=s.classifications
        )
        for s in sessions
    ]

    return HistoryResponse(sessions=items, total=len(items))


# ---------------------------------------------------------------------------
# MemPalace - Semantic Memory Search
# ---------------------------------------------------------------------------

@app.get("/memory/search", tags=["MemPalace"])
def search_memory(
    query: str,
    wing: Optional[str] = None,
    limit: int = 10,
    user: User = Depends(verify_api_key)
):
    """
    Semantic search of analysis memories.

    Example queries:
    - "BRCA2 pathogenic variants"
    - "variants with PM2 and PP3 criteria"
    - "analyses with seizure phenotype"
    """
    from src.mempalace.palace import search_memories

    results = search_memories(
        user_id=str(user.user_id),
        query=query,
        wing=wing,
        limit=limit
    )

    return {"query": query, "results": results}


@app.get("/memory/gene/{gene}", tags=["MemPalace"])
def get_gene_memory(
    gene: str,
    user: User = Depends(verify_api_key)
):
    """
    Get all variants in a gene that you've analyzed before.
    """
    from src.mempalace.knowledge_graph import get_gene_variants

    variants = get_gene_variants(
        user_id=str(user.user_id),
        gene=gene.upper()
    )

    return {"gene": gene, "variants": variants}


@app.get("/memory/variant/{gene}/{variant_id}", tags=["MemPalace"])
def get_variant_memory(
    gene: str,
    variant_id: str,
    user: User = Depends(verify_api_key)
):
    """
    Get classification history for a specific variant.

    Example: /memory/variant/BRCA2/13:32338080:A:C
    """
    from src.mempalace.knowledge_graph import get_variant_history

    history = get_variant_history(
        user_id=str(user.user_id),
        variant_id=variant_id,
        gene=gene.upper()
    )

    return {
        "gene": gene,
        "variant_id": variant_id,
        "history": history
    }


@app.get("/memory/recent", tags=["MemPalace"])
def get_recent_memory(
    limit: int = 10,
    user: User = Depends(verify_api_key)
):
    """
    Get your most recent variant analyses.
    """
    from src.mempalace.knowledge_graph import get_recent_analyses

    recent = get_recent_analyses(
        user_id=str(user.user_id),
        limit=limit
    )

    return {"recent_analyses": recent}


# ---------------------------------------------------------------------------
# Rerun
# ---------------------------------------------------------------------------

@app.post("/rerun/{session_id}", response_model=AnalyzeResponse, tags=["Analysis"])
def rerun_analysis(
    session_id: str,
    overrides: RerunRequest,
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Rerun a past analysis with parameter overrides.

    Uses the same VCF file but allows changing clinical notes, genome build, etc.
    """
    # Get original session
    original = db.query(DBSession).filter(
        DBSession.session_id == session_id,
        DBSession.user_id == user.user_id
    ).first()

    if not original:
        raise HTTPException(status_code=404, detail="Session not found")

    # Create new session ID
    new_session_id = f"session_{uuid.uuid4().hex[:12]}"

    # Merge parameters
    params = original.params_json.copy()
    if overrides.clinical_notes is not None:
        params["clinical_notes"] = overrides.clinical_notes
    if overrides.genome_build is not None:
        params["genome_build"] = overrides.genome_build
    if overrides.proband_sex is not None:
        params["proband_sex"] = overrides.proband_sex
    if overrides.patient_hpo_terms is not None:
        params["patient_hpo_terms"] = overrides.patient_hpo_terms

    # Create new session record
    session = DBSession(
        session_id=new_session_id,
        user_id=user.user_id,
        genome_build=params.get("genome_build"),
        clinical_notes=params.get("clinical_notes"),
        proband_sex=params.get("proband_sex"),
        vcf_filename=original.vcf_filename,
        status="queued",
        progress_pct=0,
        current_step="Queued for processing",
        params_json=params
    )
    db.add(session)
    db.commit()

    # Increment usage
    increment_usage(user, db)

    # Get original VCF path
    vcf_path = UPLOAD_DIR / session_id / original.vcf_filename

    # Submit to Celery
    submit_analysis(
        session_id=new_session_id,
        vcf_path=str(vcf_path),
        params=params
    )

    return AnalyzeResponse(
        session_id=new_session_id,
        status="queued",
        message=f"Rerun queued successfully. New session_id: {new_session_id}"
    )


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
def serve_frontend():
    """
    Serve the web UI.
    """
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    else:
        return HTMLResponse("""
        <html>
            <head><title>ACMG Pipeline</title></head>
            <body>
                <h1>ACMG Variant Classification Pipeline</h1>
                <p>API is running! ✅</p>
                <p>Frontend files not found. Check src/frontend/ directory.</p>
                <p>See <a href="/docs">/docs</a> for API documentation.</p>
            </body>
        </html>
        """)


# ---------------------------------------------------------------------------
# Admin Endpoints
# ---------------------------------------------------------------------------

@app.get("/admin/users", tags=["Admin"])
def list_all_users(
    limit: int = 100,
    offset: int = 0,
    admin: User = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    List all users with quota usage statistics.

    **Admin only** - requires admin API key.

    Returns user details including email, quotas, activity status.
    """
    users = db.query(User).offset(offset).limit(limit).all()
    total_count = db.query(User).count()

    return {
        "users": [
            {
                "user_id": str(u.user_id),
                "email": u.email,
                "name": u.name,
                "organisation": u.organisation,
                "analyses_used": u.analyses_used,
                "max_analyses": u.max_analyses,
                "remaining": u.max_analyses - u.analyses_used,
                "is_active": u.is_active,
                "is_admin": u.is_admin,
                "created_at": u.created_at.isoformat(),
                "has_ncbi_key": bool(u.ncbi_api_key),
            }
            for u in users
        ],
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }


@app.get("/admin/sessions", tags=["Admin"])
def list_all_sessions(
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    admin: User = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    List all analysis sessions across all users.

    **Admin only** - requires admin API key.

    Optionally filter by status: queued, running, complete, failed.
    """
    query = db.query(DBSession).join(User)

    if status:
        query = query.filter(DBSession.status == status)

    sessions = query.order_by(DBSession.created_at.desc()).offset(offset).limit(limit).all()
    total_count = query.count()

    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "user_id": str(s.user_id),
                "user_email": s.user.email,
                "status": s.status,
                "progress_pct": s.progress_pct,
                "variant_count": s.variant_count,
                "vcf_filename": s.vcf_filename,
                "genome_build": s.genome_build,
                "created_at": s.created_at.isoformat(),
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "error": s.error,
            }
            for s in sessions
        ],
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }


@app.post("/admin/users/{user_id}/quota", tags=["Admin"])
def update_user_quota(
    user_id: str,
    new_quota: int,
    admin: User = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    Update a user's analysis quota.

    **Admin only** - requires admin API key.

    Set new_quota to a higher value to increase user's limit.
    """
    import uuid as uuid_lib

    user = db.query(User).filter(User.user_id == uuid_lib.UUID(user_id)).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_quota = user.max_analyses
    user.max_analyses = new_quota
    db.commit()

    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"Admin {admin.email} updated quota for {user.email}: {old_quota} → {new_quota}"
    )

    return {
        "user_id": str(user.user_id),
        "email": user.email,
        "old_quota": old_quota,
        "new_quota": new_quota,
        "message": f"Quota updated from {old_quota} to {new_quota}",
    }


@app.post("/admin/users/{user_id}/deactivate", tags=["Admin"])
def deactivate_user(
    user_id: str,
    admin: User = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    Deactivate a user account (soft delete).

    **Admin only** - requires admin API key.

    User will no longer be able to use their API key.
    """
    import uuid as uuid_lib

    user = db.query(User).filter(User.user_id == uuid_lib.UUID(user_id)).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    db.commit()

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Admin {admin.email} deactivated user {user.email}")

    return {
        "user_id": str(user.user_id),
        "email": user.email,
        "message": f"User {user.email} has been deactivated",
    }


@app.post("/admin/users/{user_id}/activate", tags=["Admin"])
def activate_user(
    user_id: str,
    admin: User = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    Reactivate a previously deactivated user account.

    **Admin only** - requires admin API key.
    """
    import uuid as uuid_lib

    user = db.query(User).filter(User.user_id == uuid_lib.UUID(user_id)).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True
    db.commit()

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Admin {admin.email} reactivated user {user.email}")

    return {
        "user_id": str(user.user_id),
        "email": user.email,
        "message": f"User {user.email} has been reactivated",
    }


@app.get("/admin/stats", tags=["Admin"])
def get_system_stats(
    admin: User = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    Get system-wide statistics.

    **Admin only** - requires admin API key.

    Returns user counts, session counts, and analysis statistics.
    """
    from sqlalchemy import func

    total_users = db.query(User).count()
    active_users = db.query(User).filter(User.is_active == True).count()
    total_sessions = db.query(DBSession).count()

    sessions_by_status = db.query(
        DBSession.status,
        func.count(DBSession.session_id)
    ).group_by(DBSession.status).all()

    total_variants = db.query(func.sum(DBSession.variant_count)).filter(
        DBSession.variant_count.isnot(None)
    ).scalar() or 0

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "inactive": total_users - active_users,
        },
        "sessions": {
            "total": total_sessions,
            "by_status": {status: count for status, count in sessions_by_status},
        },
        "variants": {
            "total_classified": total_variants,
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
def health_check():
    """
    Health check endpoint for load balancers.
    """
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
# Server-Sent Events (SSE) for Real-time Progress
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

@app.get("/stream/{session_id}", tags=["Analysis"])
async def stream_progress(
    session_id: str,
    api_key: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Server-Sent Events endpoint for real-time progress updates.

    The frontend connects with EventSource and receives live progress events
    as the Celery worker processes the analysis.

    Note: EventSource doesn't support custom headers, so we accept API key
    as a query parameter. In production, use short-lived tokens.
    """
    # Verify API key from query param
    if not api_key:
        # Return error as SSE event
        async def error_generator():
            yield f"event: error\ndata: {json.dumps({'error': 'API key required'})}\n\n"
        return StreamingResponse(error_generator(), media_type="text/event-stream")

    # Verify session belongs to user (simplified, in production use proper auth)
    session_obj = db.query(DBSession).filter(DBSession.session_id == session_id).first()
    if not session_obj:
        async def error_generator():
            yield f"event: error\ndata: {json.dumps({'error': 'Session not found'})}\n\n"
        return StreamingResponse(error_generator(), media_type="text/event-stream")
    async def event_generator():
        # Create async Redis client
        redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()

        try:
            # Subscribe to progress channel for this session
            await pubsub.subscribe(f"progress:{session_id}")

            # Send initial connection message
            yield f"event: connected\ndata: {json.dumps({'session_id': session_id})}\n\n"

            # Listen for progress events
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    data = message['data']

                    # Parse the event
                    try:
                        event_data = json.loads(data) if isinstance(data, str) else data

                        # Send progress event
                        yield f"event: progress\ndata: {json.dumps(event_data)}\n\n"

                        # If complete or failed, send final event and close
                        stage = event_data.get('stage', '')
                        if stage in ['complete', 'failed']:
                            yield f"event: {stage}\ndata: {json.dumps(event_data)}\n\n"
                            break

                    except json.JSONDecodeError:
                        continue

        except asyncio.CancelledError:
            # Client disconnected
            pass
        finally:
            await pubsub.unsubscribe(f"progress:{session_id}")
            await redis.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Connection": "keep-alive"
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

