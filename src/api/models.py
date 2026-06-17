"""
src/api/models.py

Pydantic models for API request/response validation.
All endpoints use these models for type safety and automatic validation.
"""

from typing import Optional, List, Dict
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime


# ---------------------------------------------------------------------------
# User Registration & Authentication
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """Request to create a new user account."""
    email: EmailStr
    name: str
    organisation: Optional[str] = None


class RegisterResponse(BaseModel):
    """Response after successful registration - API key shown only once."""
    user_id: str
    api_key: str  # Plain text - never stored, shown only once
    message: str


# ---------------------------------------------------------------------------
# Variant Analysis Request
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    """Request to analyze a VCF file."""
    genome_build: str = Field(default="GRCh38", pattern="^(GRCh37|GRCh38)$")
    clinical_notes: str = Field(default="", description="Patient clinical history")
    proband_sex: str = Field(default="unknown", pattern="^(male|female|unknown)$")
    output_formats: List[str] = Field(default=["xlsx", "tsv", "html"])
    patient_hpo_terms: List[str] = Field(default=[], description="Optional pre-specified HPO IDs like HP:0001250")

    # Optional BAM paths for phasing
    proband_bam_path: Optional[str] = None
    parent1_bam_path: Optional[str] = None
    parent2_bam_path: Optional[str] = None

    # Optional parental VCFs for trio mode
    parent1_vcf_path: Optional[str] = None
    parent2_vcf_path: Optional[str] = None

    # Case database for PS4 criterion
    case_database_csv: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "genome_build": "GRCh38",
                "clinical_notes": "Patient presents with seizures, developmental delay, and hypotonia.",
                "proband_sex": "female",
                "output_formats": ["xlsx", "html"],
                "patient_hpo_terms": ["HP:0001250", "HP:0001263"]
            }
        }


class AnalyzeResponse(BaseModel):
    """Response after submitting analysis job."""
    session_id: str
    status: str  # "queued"
    message: str


# ---------------------------------------------------------------------------
# Job Status & Results
# ---------------------------------------------------------------------------

class StatusResponse(BaseModel):
    """Current status of an analysis job."""
    session_id: str
    status: str  # "queued" | "running" | "complete" | "failed"
    progress_pct: int = Field(ge=0, le=100)
    current_step: Optional[str] = None
    variant_count: Optional[int] = None
    report_paths: Optional[Dict[str, str]] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class HistoryItem(BaseModel):
    """Single session in user's history."""
    session_id: str
    vcf_filename: Optional[str]
    genome_build: Optional[str]
    variant_count: Optional[int]
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    classifications: Optional[Dict[str, str]] = None  # {variant_id: classification}


class HistoryResponse(BaseModel):
    """List of past sessions for authenticated user."""
    sessions: List[HistoryItem]
    total: int


# ---------------------------------------------------------------------------
# Rerun Request
# ---------------------------------------------------------------------------

class RerunRequest(BaseModel):
    """Request to rerun a past session with parameter overrides."""
    clinical_notes: Optional[str] = None
    genome_build: Optional[str] = None
    proband_sex: Optional[str] = None
    patient_hpo_terms: Optional[List[str]] = None

    class Config:
        json_schema_extra = {
            "example": {
                "clinical_notes": "Updated clinical history with new symptoms.",
                "patient_hpo_terms": ["HP:0001250", "HP:0001263", "HP:0001252"]
            }
        }


# ---------------------------------------------------------------------------
# WebSocket Progress Messages
# ---------------------------------------------------------------------------

class ProgressMessage(BaseModel):
    """Real-time progress update sent via WebSocket."""
    session_id: str
    status: str
    progress_pct: int
    current_step: str
    timestamp: datetime = Field(default_factory=datetime.now)

    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "abc123",
                "status": "running",
                "progress_pct": 45,
                "current_step": "Running agents for variant 5/12 (BRCA2)",
                "timestamp": "2026-06-17T12:30:00"
            }
        }


# ---------------------------------------------------------------------------
# Error Responses
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    """Standard error response format."""
    error: str
    detail: Optional[str] = None
    session_id: Optional[str] = None

