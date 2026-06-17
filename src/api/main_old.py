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
from src.api.auth import register_user, verify_api_key, increment_usage
from src.api.models import (
    RegisterRequest, RegisterResponse,
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

