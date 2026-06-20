"""
src/api/db.py

Database models and connection setup for the ACMG Pipeline API.
Uses SQLAlchemy with PostgreSQL + pgvector for semantic search.

Tables:
- users: User accounts, API keys, quotas
- sessions: Analysis job tracking (MemPalace audit trail)
- palace_memories: Per-user semantic memory store
- palace_knowledge: Variant/gene relationship graph over time
"""

import os
from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, JSON, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

# Database URL from environment or default to local
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres@localhost/acmg_pipeline"
)

# Create engine
engine = create_engine(DATABASE_URL, echo=False)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


# ---------------------------------------------------------------------------
# Database Models
# ---------------------------------------------------------------------------

class User(Base):
    """User accounts with API key authentication and quota management."""
    __tablename__ = "users"

    user_id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String)
    organisation = Column(String)
    api_key_hash = Column(String, nullable=False)  # bcrypt hash
    max_analyses = Column(Integer, default=100)
    analyses_used = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)  # Admin flag for privileged access
    ncbi_api_key = Column(String, nullable=True)  # Optional user-provided NCBI API key

    # Relationships
    sessions = relationship("Session", back_populates="user")
    memories = relationship("PalaceMemory", back_populates="user")
    knowledge = relationship("PalaceKnowledge", back_populates="user")


class Session(Base):
    """Analysis sessions - tracks job status and results."""
    __tablename__ = "sessions"

    session_id = Column(String, primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)
    genome_build = Column(String)
    clinical_notes = Column(Text)
    proband_sex = Column(String)
    vcf_filename = Column(String)

    # Trio mode tracking
    trio_mode = Column(Boolean, default=False)  # True if parental VCFs provided
    parent1_vcf_filename = Column(String, nullable=True)  # Mother's VCF filename
    parent2_vcf_filename = Column(String, nullable=True)  # Father's VCF filename
    proband_bam_filename = Column(String, nullable=True)  # Proband BAM for phasing
    parent1_bam_filename = Column(String, nullable=True)  # Mother's BAM for phasing
    parent2_bam_filename = Column(String, nullable=True)  # Father's BAM for phasing

    # Trio-specific results (for dashboard display)
    denovo_count = Column(Integer, default=0)  # Number of de novo variants (PS2)
    compound_het_count = Column(Integer, default=0)  # Number of compound het pairs (PM3)
    segregation_count = Column(Integer, default=0)  # Number of variants with segregation evidence

    variant_count = Column(Integer)
    status = Column(String, default="queued", index=True)  # queued|running|complete|failed
    progress_pct = Column(Integer, default=0)
    current_step = Column(String)
    report_paths = Column(JSON)  # {"xlsx": "path", "tsv": "path", "html": "path"}
    classifications = Column(JSON)  # {variant_id: final_classification}
    params_json = Column(JSON)  # Full AnalyzeRequest for rerun capability
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

    # Relationships
    user = relationship("User", back_populates="sessions")


class PalaceMemory(Base):
    """MemPalace semantic memory store - per-user hierarchical memory."""
    __tablename__ = "palace_memories"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)
    wing = Column(String, nullable=False, index=True)  # "analysis_history" | "preferences" | "variants"
    room = Column(String, index=True)  # gene name, session_id, or preference category
    content = Column(Text, nullable=False)  # verbatim stored text
    embedding = Column(Vector(384))  # sentence-transformers/all-MiniLM-L6-v2
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_deleted = Column(Boolean, default=False)

    # Relationships
    user = relationship("User", back_populates="memories")


class PalaceKnowledge(Base):
    """MemPalace knowledge graph - variant/gene relationships over time."""
    __tablename__ = "palace_knowledge"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)
    subject = Column(String, index=True)  # e.g. "BRCA2:13:32338080:A:C"
    relation = Column(String, index=True)  # e.g. "classified_as" | "associated_with" | "seen_in_session"
    object = Column(String, index=True)   # e.g. "VUS" | "Hereditary Breast Cancer" | "session_abc123"
    valid_from = Column(DateTime(timezone=True), server_default=func.now())
    valid_until = Column(DateTime(timezone=True))  # NULL = still active
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="knowledge")


# ---------------------------------------------------------------------------
# Database Initialization
# ---------------------------------------------------------------------------

def init_db():
    """
    Create all tables and pgvector index.
    Run once on first deployment.
    """
    Base.metadata.create_all(bind=engine)

    # Create pgvector index for fast similarity search
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS palace_memories_embedding_idx
            ON palace_memories
            USING ivfflat (embedding vector_cosine_ops)
            WHERE is_deleted = FALSE;
        """))
        conn.commit()

    print("✅ Database initialized successfully!")


def get_db():
    """
    Dependency for FastAPI endpoints.
    Yields a database session and closes it after request.

    Usage in FastAPI:
        @app.get("/endpoint")
        def endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Fix missing import
from sqlalchemy import text


if __name__ == "__main__":
    # Run this script directly to initialize the database
    print("Initializing ACMG Pipeline database...")
    init_db()

