"""
QC API endpoints for validating ACMG analysis results.
"""

import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.db import get_db, User, Session as DBSession
from src.api.auth import verify_api_key
from src.qc import QCAgent, QCStore, export_qc_results_csv
from src.config import OUTPUT_DIR
import os
import json

logger = logging.getLogger(__name__)
router = APIRouter()


class QCValidateRequest(BaseModel):
    session_id: str


class QCResultResponse(BaseModel):
    id: str
    session_id: str
    patient_id: Optional[str]
    analysis_mode: Optional[str]
    qc_status: str
    qc_score: float
    confidence: float
    input_qc: str
    annotation_qc: str
    evidence_qc: str
    classification_qc: str
    report_qc: str
    issues: list
    created_at: Optional[str]


@router.post("/validate", response_model=QCResultResponse)
def validate_analysis(
    request: QCValidateRequest,
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Run QC validation on a completed analysis session.

    This endpoint:
    1. Loads session data and results from the database
    2. Runs comprehensive QC checks (input, annotation, evidence, classification, report)
    3. Calculates QC score and status (PASS/WARNING/FAIL)
    4. Stores results in qc_results table
    5. Exports results to CSV

    Returns QC validation results with scores and identified issues.
    """
    session_id = request.session_id

    # Verify session belongs to user
    session = db.query(DBSession).filter(
        DBSession.session_id == session_id,
        DBSession.user_id == user.user_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check if analysis is complete
    if session.status != "complete":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot run QC on incomplete analysis (status: {session.status})"
        )

    # Load session results
    session_dir = os.path.join(OUTPUT_DIR, session_id)

    try:
        # Load variants data
        variants_file = os.path.join(session_dir, "final_results.json")
        if not os.path.exists(variants_file):
            raise HTTPException(status_code=404, detail="Results file not found")

        with open(variants_file, "r") as f:
            results_data = json.load(f)

        variants_data = results_data.get("variants", [])

        # Load annotation data (if exists)
        annotation_file = os.path.join(session_dir, "annotation_results.json")
        annotation_data = {}
        if os.path.exists(annotation_file):
            with open(annotation_file, "r") as f:
                annotation_data = json.load(f)

        # Load reports data
        reports_data = {
            "pdf_path": os.path.join(session_dir, "report.pdf"),
            "csv_path": os.path.join(session_dir, "report.csv"),
            "json_path": os.path.join(session_dir, "final_results.json")
        }

        # Prepare session data for QC
        session_data = {
            "session_id": session_id,
            "patient_id": session.patient_id or "unknown",
            "analysis_mode": "trio" if session.trio_mode else "solo",
            "genome_build": session.genome_build,
            "vcf_filename": session.vcf_filename,
            "variant_count": session.variant_count,
            "proband_sex": session.proband_sex,
            "father_id": session.father_id if session.trio_mode else None,
            "mother_id": session.mother_id if session.trio_mode else None,
        }

        # Run QC
        qc_agent = QCAgent()
        qc_result = qc_agent.run_qc(
            session_data=session_data,
            variants_data=variants_data,
            annotation_data=annotation_data,
            reports_data=reports_data
        )

        logger.info(f"QC validation completed for session {session_id}: {qc_result['qc_status']}")

        return QCResultResponse(**qc_result)

    except FileNotFoundError as e:
        logger.error(f"QC validation failed - file not found: {e}")
        raise HTTPException(status_code=404, detail=f"Required file not found: {str(e)}")
    except Exception as e:
        logger.error(f"QC validation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"QC validation failed: {str(e)}")


@router.get("/result/{session_id}", response_model=QCResultResponse)
def get_qc_result(
    session_id: str,
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Get QC result for a session.
    """
    # Verify session belongs to user
    session = db.query(DBSession).filter(
        DBSession.session_id == session_id,
        DBSession.user_id == user.user_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get QC result
    store = QCStore()
    qc_result = store.get_qc_result(session_id)

    if not qc_result:
        raise HTTPException(status_code=404, detail="QC result not found for this session")

    return QCResultResponse(**qc_result)


@router.get("/results")
def list_qc_results(
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    List all QC results for user's sessions.
    """
    # Get user's session IDs
    user_sessions = db.query(DBSession.session_id).filter(
        DBSession.user_id == user.user_id
    ).all()

    session_ids = [s[0] for s in user_sessions]

    # Get all QC results
    store = QCStore()
    all_results = store.get_all_results()

    # Filter to user's sessions
    user_results = [
        r for r in all_results
        if r["session_id"] in session_ids
    ]

    return {
        "results": user_results,
        "total": len(user_results)
    }


@router.get("/export/{session_id}")
def export_qc_result(
    session_id: str,
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Export QC result to CSV.
    """
    # Verify session belongs to user
    session = db.query(DBSession).filter(
        DBSession.session_id == session_id,
        DBSession.user_id == user.user_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get QC result
    store = QCStore()
    qc_result = store.get_qc_result(session_id)

    if not qc_result:
        raise HTTPException(status_code=404, detail="QC result not found")

    # Export to CSV
    try:
        csv_path = export_qc_results_csv([qc_result])
        return {
            "message": "QC result exported successfully",
            "csv_path": csv_path
        }
    except Exception as e:
        logger.error(f"CSV export failed: {e}")
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")
