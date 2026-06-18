"""
src/api/worker.py

Celery worker for asynchronous variant analysis.
Wraps the pipeline runner and updates job status in the database.
"""

import os
import json
import traceback
from datetime import datetime
from celery import Celery
from sqlalchemy.orm import Session
import redis

from src.pipeline.runner import run_session
from src.api.db import SessionLocal, Session as DBSession
from src.api.models import AnalyzeRequest
from src.utils.logging_config import ProgressCallback
from src.mempalace.palace import mine_session_summary
from src.mempalace.knowledge_graph import record_classification

# Redis broker URL
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Redis client for SSE pub/sub
redis_client = redis.from_url(REDIS_URL)

# Create Celery app
celery_app = Celery(
    "acmg_pipeline",
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3300,  # 55 min soft limit
)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def update_session_status(
    db: Session,
    session_id: str,
    status: str,
    progress_pct: int = 0,
    current_step: str = None,
    variant_count: int = None,
    report_paths: dict = None,
    classifications: dict = None,
    error: str = None
):
    """Update session status in database."""
    session = db.query(DBSession).filter(DBSession.session_id == session_id).first()
    if not session:
        return

    session.status = status
    session.progress_pct = progress_pct
    if current_step:
        session.current_step = current_step
    if variant_count is not None:
        session.variant_count = variant_count
    if report_paths:
        session.report_paths = report_paths
    if classifications:
        session.classifications = classifications
    if error:
        session.error = error

    if status in ["complete", "failed"]:
        session.completed_at = datetime.utcnow()

    db.commit()


# ---------------------------------------------------------------------------
# Celery Tasks
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="analyze_variant")
def analyze_variant_task(self, session_id: str, vcf_path: str, params: dict):
    """
    Celery task to run variant analysis pipeline.

    Args:
        session_id: Unique session identifier
        vcf_path: Path to uploaded VCF file
        params: AnalyzeRequest parameters as dict

    Returns:
        dict with session_id, status, and report_paths
    """
    db = SessionLocal()

    try:
        # Update status to running
        update_session_status(
            db, session_id,
            status="running",
            progress_pct=5,
            current_step="Starting VEP annotation..."
        )

        # Create progress callback for SSE
        def publish_progress(event):
            """Publish progress events to Redis for SSE streaming."""
            # Update database
            update_session_status(
                db, session_id,
                status="running",
                progress_pct=int(event.get('progress', 0) * 100),
                current_step=event.get('message', 'Processing...')
            )

            # Publish to Redis for SSE
            redis_client.publish(
                f"progress:{session_id}",
                json.dumps({
                    'stage': event.get('stage'),
                    'progress': event.get('progress'),
                    'message': event.get('message'),
                    'variant_id': event.get('variant_id'),
                    'gene': event.get('gene'),
                    'timestamp': datetime.utcnow().isoformat()
                })
            )

        progress_callback = ProgressCallback(publish_progress)

        # Run the pipeline with progress callback
        result = run_session(
            session_id=session_id,
            proband_vcf_path=vcf_path,
            genome_build=params.get("genome_build", "GRCh38"),
            clinical_notes=params.get("clinical_notes", ""),
            proband_sex=params.get("proband_sex", "unknown"),
            proband_bam_path=params.get("proband_bam_path"),
            parent1_vcf_path=params.get("parent1_vcf_path"),
            parent2_vcf_path=params.get("parent2_vcf_path"),
            parent1_bam_path=params.get("parent1_bam_path"),
            parent2_bam_path=params.get("parent2_bam_path"),
            case_database_csv=params.get("case_database_csv"),
            patient_hpo_terms=params.get("patient_hpo_terms", []),
            progress_callback=progress_callback,
        )

        # Extract classifications
        classifications = {}
        for state in result.get("completed_states", []):
            variant_id = state.get("variant_id")
            classification = state.get("final_classification", "VUS")
            if variant_id:
                classifications[variant_id] = classification

        # Get report paths from result (handle both dict and individual keys)
        report_paths_raw = result.get("report_paths", {})
        report_paths = {
            "xlsx": str(report_paths_raw.get("xlsx")) if report_paths_raw.get("xlsx") else None,
            "tsv": str(report_paths_raw.get("tsv")) if report_paths_raw.get("tsv") else None,
            "html": str(report_paths_raw.get("html")) if report_paths_raw.get("html") else None,
        }

        # Update status to complete
        update_session_status(
            db, session_id,
            status="complete",
            progress_pct=100,
            current_step="Analysis complete",
            variant_count=result.get("variant_count", 0),
            report_paths=report_paths,
            classifications=classifications
        )

        # Store in MemPalace (get user_id from session)
        db_session = db.query(DBSession).filter(DBSession.session_id == session_id).first()
        if db_session and db_session.user_id:
            try:
                # Mine session summary
                mine_session_summary(
                    user_id=str(db_session.user_id),
                    session_id=session_id,
                    variant_count=result.get("variant_count", 0),
                    classifications=classifications,
                    genome_build=params.get("genome_build", "GRCh38"),
                    clinical_notes=params.get("clinical_notes", ""),
                    db=db
                )

                # Record each variant classification in knowledge graph
                for state in result.get("completed_states", []):
                    variant_id = state.get("variant_id")
                    gene = state.get("gene")
                    classification = state.get("final_classification", "VUS")

                    if variant_id and gene:
                        record_classification(
                            user_id=str(db_session.user_id),
                            variant_id=variant_id,
                            gene=gene,
                            classification=classification,
                            session_id=session_id,
                            db=db
                        )

            except Exception as mem_error:
                # Don't fail the whole task if MemPalace fails
                print(f"MemPalace error (non-fatal): {mem_error}")

        return {
            "session_id": session_id,
            "status": "complete",
            "variant_count": result.get("variant_count", 0),
            "report_paths": report_paths,
        }

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"

        # Update status to failed
        update_session_status(
            db, session_id,
            status="failed",
            progress_pct=0,
            current_step="Analysis failed",
            error=error_msg
        )

        # Re-raise so Celery marks task as failed
        raise

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task Management
# ---------------------------------------------------------------------------

def submit_analysis(
    session_id: str,
    vcf_path: str,
    params: dict
) -> str:
    """
    Submit an analysis task to Celery queue.

    Args:
        session_id: Unique session identifier
        vcf_path: Path to uploaded VCF file
        params: AnalyzeRequest parameters as dict

    Returns:
        Celery task ID
    """
    task = analyze_variant_task.apply_async(
        args=[session_id, vcf_path, params],
        task_id=session_id  # Use session_id as task_id for easy lookup
    )
    return task.id


def get_task_status(task_id: str) -> dict:
    """
    Get status of a Celery task.

    Args:
        task_id: Celery task ID (same as session_id)

    Returns:
        dict with state, info
    """
    task = celery_app.AsyncResult(task_id)
    return {
        "state": task.state,  # PENDING, STARTED, SUCCESS, FAILURE
        "info": task.info,
    }


if __name__ == "__main__":
    # Start Celery worker
    # Run with: celery -A src.api.worker worker --loglevel=info
    print("Celery worker for ACMG Pipeline")
    print(f"Broker: {REDIS_URL}")

