"""
src/api/worker.py

Celery worker for asynchronous variant analysis.
Wraps the pipeline runner and updates job status in the database.
"""

import os
import json
import logging
import subprocess
import traceback
import time
from datetime import datetime
from pathlib import Path
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed
from celery import Celery
from sqlalchemy.orm import Session
import redis

from src.pipeline.runner import run_session
from src.api.db import SessionLocal, Session as DBSession
from src.api.models import AnalyzeRequest
from src.utils.logging_config import ProgressCallback
from src.mempalace.palace import mine_session_summary
from src.mempalace.knowledge_graph import record_classification

logger = logging.getLogger(__name__)

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

def _ensure_vcf_indexed(vcf_path: str) -> None:
    """
    Ensure VCF.gz file has tabix index (.tbi).
    Creates index if missing. Non-fatal if indexing fails.

    Args:
        vcf_path: Path to .vcf.gz file
    """
    if not vcf_path.endswith('.vcf.gz'):
        logger.debug(f"Skipping indexing for non-gzipped VCF: {vcf_path}")
        return

    vcf_file = Path(vcf_path)
    tbi_file = Path(vcf_path + ".tbi")
    csi_file = Path(vcf_path + ".csi")

    # ⭐ CHECK: Skip if already indexed
    if tbi_file.exists() or csi_file.exists():
        logger.info(f"✓ VCF index exists: {vcf_file.name}")
        return

    logger.info(f"Creating tabix index for {vcf_file.name}...")

    try:
        # Use tabix to create index
        result = subprocess.run(
            ["tabix", "-p", "vcf", vcf_path],
            check=True,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout for large VCFs
        )

        if tbi_file.exists():
            logger.info(f"✓ Successfully created index: {tbi_file.name}")
        else:
            logger.warning(f"tabix completed but no .tbi file found for {vcf_path}")

    except subprocess.CalledProcessError as e:
        logger.warning(
            f"tabix indexing failed for {vcf_path} (non-fatal): {e.stderr}\n"
            f"Pipeline will continue but performance may be slower."
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            f"tabix indexing timed out for {vcf_path} (non-fatal)\n"
            f"VCF may be very large. Pipeline will continue."
        )
    except FileNotFoundError:
        logger.warning(
            f"tabix not found in PATH - skipping indexing for {vcf_path}\n"
            f"Install with: conda install -c bioconda tabix"
        )
    except Exception as e:
        logger.warning(f"Unexpected error during indexing (non-fatal): {e}")


def _ensure_bam_indexed(bam_path: str, threads: int = 8) -> None:
    """
    Ensure BAM file has index (.bai).
    Creates index if missing using multi-threaded samtools.

    Args:
        bam_path: Path to .bam file
        threads: Number of threads for samtools index (default: 8)

    Raises:
        RuntimeError: If indexing fails (hard failure - pipeline stops)
    """
    if not bam_path or not bam_path.endswith('.bam'):
        return

    bam_file = Path(bam_path)
    if not bam_file.exists():
        raise RuntimeError(f"BAM file not found: {bam_path}")

    bai_file = Path(bam_path + ".bai")

    # ⭐ CHECK: Skip if already indexed (INSTANT for re-runs!)
    if bai_file.exists():
        logger.info(f"✓ BAM index exists: {bam_file.name}")
        return

    # BAM file exists but no index - create it
    logger.info(f"Creating BAM index for {bam_file.name} (using {threads} threads)...")
    start_time = time.time()

    try:
        result = subprocess.run(
            ["samtools", "index", "-@", str(threads), str(bam_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour max for very large BAMs
        )

        elapsed = time.time() - start_time
        logger.info(f"✓ Indexed {bam_file.name} in {elapsed:.1f}s")

        if not bai_file.exists():
            raise RuntimeError(
                f"samtools index completed but no .bai file created for {bam_file.name}"
            )

    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"BAM indexing failed for {bam_file.name}:\n{e.stderr}\n\n"
            f"Please index manually before upload:\n"
            f"  samtools index {bam_path}"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"BAM indexing timed out for {bam_file.name} (>1 hour)\n"
            f"BAM file may be too large. Please index manually:\n"
            f"  samtools index -@ 8 {bam_path}"
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"samtools not found in PATH.\n"
            f"Install with: conda install -c bioconda samtools\n"
            f"Or index manually and upload .bai file alongside BAM."
        )


def _index_all_bams_parallel(bam_paths: List[str], session_id: str = None) -> None:
    """
    Index multiple BAM files in parallel using ThreadPoolExecutor.
    This allows trio BAMs to be indexed simultaneously (wall-clock time ≈ single BAM).

    Args:
        bam_paths: List of BAM file paths (can contain None values - will be filtered)
        session_id: Optional session ID for logging

    Raises:
        RuntimeError: If any BAM indexing fails
    """
    # Filter out None/empty paths and non-existent files
    valid_bams = [p for p in bam_paths if p and Path(p).exists()]

    if not valid_bams:
        logger.debug(f"[{session_id}] No BAM files to index")
        return

    logger.info(f"[{session_id}] Indexing {len(valid_bams)} BAM file(s) in parallel...")

    # Parallel indexing: all BAMs indexed simultaneously
    # Each BAM uses 8 threads, so 3 BAMs = 24 cores max (acceptable on 32-core instance)
    with ThreadPoolExecutor(max_workers=len(valid_bams)) as executor:
        # Submit all indexing jobs
        futures = {
            executor.submit(_ensure_bam_indexed, bam, threads=8): bam
            for bam in valid_bams
        }

        # Wait for completion and check for errors
        for future in as_completed(futures):
            bam = futures[future]
            try:
                future.result()  # Raises exception if indexing failed
            except Exception as e:
                # Hard failure - stop pipeline
                logger.error(f"[{session_id}] Failed to index {Path(bam).name}: {e}")
                raise


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
        # Ensure VCF is indexed (auto-creates .tbi if missing)
        logger.info(f"[{session_id}] Checking VCF index...")
        _ensure_vcf_indexed(vcf_path)

        # Also index parent VCFs if trio mode
        if params.get("parent1_vcf_path"):
            _ensure_vcf_indexed(params["parent1_vcf_path"])
        if params.get("parent2_vcf_path"):
            _ensure_vcf_indexed(params["parent2_vcf_path"])

        # Index all BAM files in parallel (if provided)
        bam_paths = [
            params.get("proband_bam_path"),
            params.get("parent1_bam_path"),
            params.get("parent2_bam_path")
        ]
        if any(bam_paths):
            update_session_status(
                db, session_id,
                status="running",
                progress_pct=2,
                current_step="Indexing BAM files..."
            )
            logger.info(f"[{session_id}] Checking BAM indexes...")
            _index_all_bams_parallel(bam_paths, session_id=session_id)

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

        # Get user for NCBI key override
        from src.api.db import User
        session_obj = db.query(DBSession).filter(DBSession.session_id == session_id).first()
        if session_obj and session_obj.user:
            user = session_obj.user

            # Override NCBI API key if user provided their own
            if user.ncbi_api_key:
                logger.info(f"[{session_id}] Using user-provided NCBI API key")
                os.environ["NCBI_API_KEY"] = user.ncbi_api_key
            else:
                # Fall back to system-wide NCBI key
                system_ncbi_key = os.getenv("SYSTEM_NCBI_API_KEY", "")
                if system_ncbi_key:
                    os.environ["NCBI_API_KEY"] = system_ncbi_key
                    logger.info(f"[{session_id}] Using system-wide NCBI API key")
                else:
                    # No key available - pubmed will use no-key rate limit (3 req/sec)
                    logger.warning(
                        f"[{session_id}] No NCBI API key configured - PubMed rate limited to 3 req/sec. "
                        f"Set SYSTEM_NCBI_API_KEY in .env.aws or provide ncbi_api_key during user registration. "
                        f"Get a free key at: https://www.ncbi.nlm.nih.gov/account/"
                    )
                    os.environ["NCBI_API_KEY"] = ""

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

        # Finalize token usage tracking
        try:
            from src.utils.token_tracker import finalize_session
            token_summary = finalize_session(session_id)
            if token_summary:
                logger.info(
                    f"[{session_id}] Token usage: {token_summary['total_tokens']} total "
                    f"({token_summary['total_input_tokens']} input, "
                    f"{token_summary['total_output_tokens']} output)"
                )
        except Exception as e:
            logger.warning(f"[{session_id}] Failed to finalize token tracking: {e}")

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
        task_id=session_id,  # Use session_id as task_id for easy lookup
        queue='acmg_jobs'  # Send to the correct queue that worker is listening on
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


