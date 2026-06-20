"""
Dashboard API endpoints for viewing analysis history and statistics.
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from src.api.db import get_db, User, Session as DBSession
from src.api.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/analyses")
def get_dashboard_analyses(
    status: Optional[str] = Query(None, description="Filter by status: complete, running, queued, failed"),
    search: Optional[str] = Query(None, description="Search by session_id, patient_id, or vcf_filename"),
    limit: int = Query(50, ge=1, le=200, description="Number of results per page"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Get user's analysis sessions for dashboard display.

    Returns paginated list of sessions with:
    - Session metadata (ID, status, dates)
    - Variant counts
    - Trio mode information (de novo, compound het counts)
    - Classification summary
    - Progress percentage
    """
    query = db.query(DBSession).filter(DBSession.user_id == user.user_id)

    # Filter by status
    if status:
        query = query.filter(DBSession.status == status)

    # Search filter
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                DBSession.session_id.ilike(search_pattern),
                DBSession.patient_id.ilike(search_pattern),
                DBSession.vcf_filename.ilike(search_pattern)
            )
        )

    # Get total count
    total = query.count()

    # Get paginated results
    sessions = query.order_by(DBSession.created_at.desc()).offset(offset).limit(limit).all()

    # Format results
    results = []
    for s in sessions:
        item = {
            "session_id": s.session_id,
            "patient_id": s.patient_id,
            "status": s.status,
            "progress_pct": s.progress_pct or 0,
            "variant_count": s.variant_count,
            "trio_mode": s.trio_mode,
            "genome_build": s.genome_build,
            "vcf_filename": s.vcf_filename,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "classifications": s.classifications or {},
        }

        # Add trio-specific metrics if available
        if s.trio_mode:
            item["denovo_count"] = s.denovo_count or 0
            item["compound_het_count"] = s.compound_het_count or 0
            item["father_id"] = s.father_id
            item["mother_id"] = s.mother_id
            item["proband_sex"] = s.proband_sex

        results.append(item)

    return {
        "sessions": results,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + limit) < total
    }


@router.get("/stats")
def get_dashboard_stats(
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Get summary statistics for dashboard header/overview.

    Returns:
    - Total analyses count
    - Completed analyses count
    - Running analyses count
    - Failed analyses count
    - Total variants classified
    - Classification distribution (P, LP, VUS, LB, B)
    - Trio-specific stats (if applicable)
    """
    # Basic counts
    total_analyses = db.query(DBSession).filter(
        DBSession.user_id == user.user_id
    ).count()

    completed = db.query(DBSession).filter(
        DBSession.user_id == user.user_id,
        DBSession.status == "complete"
    ).count()

    running = db.query(DBSession).filter(
        DBSession.user_id == user.user_id,
        DBSession.status == "running"
    ).count()

    failed = db.query(DBSession).filter(
        DBSession.user_id == user.user_id,
        DBSession.status == "failed"
    ).count()

    queued = db.query(DBSession).filter(
        DBSession.user_id == user.user_id,
        DBSession.status == "queued"
    ).count()

    # Total variants classified
    total_variants = db.query(func.sum(DBSession.variant_count)).filter(
        DBSession.user_id == user.user_id,
        DBSession.variant_count.isnot(None)
    ).scalar() or 0

    # Aggregate classifications
    completed_sessions = db.query(DBSession).filter(
        DBSession.user_id == user.user_id,
        DBSession.status == "complete",
        DBSession.classifications.isnot(None)
    ).all()

    classification_totals = {
        "pathogenic": 0,
        "likely_pathogenic": 0,
        "vus": 0,
        "likely_benign": 0,
        "benign": 0
    }

    for session in completed_sessions:
        if session.classifications:
            for key, value in session.classifications.items():
                if key in classification_totals:
                    classification_totals[key] += value

    # Trio-specific stats
    trio_count = db.query(DBSession).filter(
        DBSession.user_id == user.user_id,
        DBSession.trio_mode == True
    ).count()

    total_denovo = db.query(func.sum(DBSession.denovo_count)).filter(
        DBSession.user_id == user.user_id,
        DBSession.trio_mode == True,
        DBSession.denovo_count.isnot(None)
    ).scalar() or 0

    total_compound_het = db.query(func.sum(DBSession.compound_het_count)).filter(
        DBSession.user_id == user.user_id,
        DBSession.trio_mode == True,
        DBSession.compound_het_count.isnot(None)
    ).scalar() or 0

    return {
        "total_analyses": total_analyses,
        "completed": completed,
        "running": running,
        "queued": queued,
        "failed": failed,
        "total_variants_classified": int(total_variants),
        "classifications": classification_totals,
        "trio_stats": {
            "trio_analyses": trio_count,
            "total_denovo_variants": int(total_denovo),
            "total_compound_het": int(total_compound_het)
        }
    }


@router.get("/session/{session_id}")
def get_session_detail(
    session_id: str,
    user: User = Depends(verify_api_key),
    db: Session = Depends(get_db)
):
    """
    Get detailed information for a specific session.

    Includes all session parameters, progress, results summary, and QC status.
    """
    session = db.query(DBSession).filter(
        DBSession.session_id == session_id,
        DBSession.user_id == user.user_id
    ).first()

    if not session:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")

    # Get QC result if available
    qc_result = None
    try:
        from src.qc import QCStore
        store = QCStore()
        qc_result = store.get_qc_result(session_id)
    except Exception as e:
        logger.warning(f"Could not load QC result for {session_id}: {e}")

    return {
        "session_id": session.session_id,
        "patient_id": session.patient_id,
        "status": session.status,
        "progress_pct": session.progress_pct,
        "variant_count": session.variant_count,
        "trio_mode": session.trio_mode,
        "genome_build": session.genome_build,
        "vcf_filename": session.vcf_filename,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "classifications": session.classifications,
        "hpo_terms": session.hpo_terms,
        "clinical_notes": session.clinical_notes,
        "proband_sex": session.proband_sex,
        "father_id": session.father_id if session.trio_mode else None,
        "mother_id": session.mother_id if session.trio_mode else None,
        "denovo_count": session.denovo_count if session.trio_mode else None,
        "compound_het_count": session.compound_het_count if session.trio_mode else None,
        "qc_result": qc_result
    }
