-- Migration: Add missing fields to sessions table
-- Run this on your PostgreSQL database

-- Add patient_id column
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS patient_id VARCHAR;

-- Add vcf_path column (full path to VCF file)
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS vcf_path VARCHAR;

-- Add analysis_mode column
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS analysis_mode VARCHAR DEFAULT 'solo';

-- Add father_id and mother_id columns for trio tracking
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS father_id VARCHAR;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS mother_id VARCHAR;

-- Add hpo_terms column (JSON array)
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS hpo_terms JSONB;

-- Create indexes for search performance
CREATE INDEX IF NOT EXISTS idx_sessions_patient_id ON sessions(patient_id);
CREATE INDEX IF NOT EXISTS idx_sessions_analysis_mode ON sessions(analysis_mode);

-- Update existing records to set analysis_mode based on trio_mode
UPDATE sessions
SET analysis_mode = CASE
    WHEN trio_mode = TRUE THEN 'trio'
    ELSE 'solo'
END
WHERE analysis_mode IS NULL;

COMMENT ON COLUMN sessions.patient_id IS 'Optional patient identifier';
COMMENT ON COLUMN sessions.vcf_path IS 'Full path to proband VCF file';
COMMENT ON COLUMN sessions.analysis_mode IS 'Analysis mode: solo or trio';
COMMENT ON COLUMN sessions.father_id IS 'Father/Parent1 identifier for trio analysis';
COMMENT ON COLUMN sessions.mother_id IS 'Mother/Parent2 identifier for trio analysis';
COMMENT ON COLUMN sessions.hpo_terms IS 'List of HPO term IDs for phenotype matching';
