import os
import json
import uuid
from typing import Dict, Any, List, Optional
from sqlalchemy import Column, String, Float, Text, DateTime, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import func

# Get DATABASE_URL from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://acmg_user:acmg_password@localhost:5432/acmg_pipeline"
)

Base = declarative_base()

class QCResult(Base):
    __tablename__ = "qc_results"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    patient_id = Column(String, index=True)
    analysis_mode = Column(String)
    qc_status = Column(String, index=True)
    qc_score = Column(Float)
    input_qc = Column(String)
    annotation_qc = Column(String)
    evidence_qc = Column(String)
    classification_qc = Column(String)
    report_qc = Column(String)
    confidence = Column(Float)
    issues = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

def init_db():
    """Initialize QC results table in PostgreSQL."""
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    engine.dispose()

class QCStore:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        self.SessionLocal = sessionmaker(bind=self.engine)
        init_db()

    def save_qc_result(self, record: Dict[str, Any]) -> str:
        """Save QC result to PostgreSQL."""
        record_id = str(uuid.uuid4())
        issues_json = json.dumps(record.get("issues", []))

        session = self.SessionLocal()
        try:
            qc_result = QCResult(
                id=record_id,
                session_id=record.get("session_id"),
                patient_id=record.get("patient_id"),
                analysis_mode=record.get("analysis_mode"),
                qc_status=record.get("qc_status"),
                qc_score=record.get("qc_score"),
                input_qc=record.get("input_qc"),
                annotation_qc=record.get("annotation_qc"),
                evidence_qc=record.get("evidence_qc"),
                classification_qc=record.get("classification_qc"),
                report_qc=record.get("report_qc"),
                confidence=record.get("confidence"),
                issues=issues_json
            )
            session.add(qc_result)
            session.commit()
            return record_id
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def get_qc_result(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get most recent QC result for a session."""
        session = self.SessionLocal()
        try:
            result = session.query(QCResult).filter(
                QCResult.session_id == session_id
            ).order_by(QCResult.created_at.desc()).first()

            if result:
                return {
                    "id": result.id,
                    "session_id": result.session_id,
                    "patient_id": result.patient_id,
                    "analysis_mode": result.analysis_mode,
                    "qc_status": result.qc_status,
                    "qc_score": result.qc_score,
                    "input_qc": result.input_qc,
                    "annotation_qc": result.annotation_qc,
                    "evidence_qc": result.evidence_qc,
                    "classification_qc": result.classification_qc,
                    "report_qc": result.report_qc,
                    "confidence": result.confidence,
                    "issues": json.loads(result.issues) if result.issues else [],
                    "created_at": result.created_at.isoformat() if result.created_at else None
                }
            return None
        finally:
            session.close()

    def get_all_results(self) -> List[Dict[str, Any]]:
        """Get all QC results ordered by creation date."""
        session = self.SessionLocal()
        try:
            results = session.query(QCResult).order_by(QCResult.created_at.desc()).all()

            return [
                {
                    "id": r.id,
                    "session_id": r.session_id,
                    "patient_id": r.patient_id,
                    "analysis_mode": r.analysis_mode,
                    "qc_status": r.qc_status,
                    "qc_score": r.qc_score,
                    "input_qc": r.input_qc,
                    "annotation_qc": r.annotation_qc,
                    "evidence_qc": r.evidence_qc,
                    "classification_qc": r.classification_qc,
                    "report_qc": r.report_qc,
                    "confidence": r.confidence,
                    "issues": json.loads(r.issues) if r.issues else [],
                    "created_at": r.created_at.isoformat() if r.created_at else None
                }
                for r in results
            ]
        finally:
            session.close()
