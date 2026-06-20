-- migrate_database.sql
--
-- Database migration to add new fields to existing tables.
-- Run this if you already have the database created and need to add new columns.
--
-- Usage:
--   psql -d acmg_pipeline -f migrate_database.sql
--
-- Or from Python:
--   python migrate_database.py

-- Add admin flag to users table (if not exists)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'is_admin'
    ) THEN
        ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE;
        RAISE NOTICE 'Added is_admin column to users table';
    ELSE
        RAISE NOTICE 'is_admin column already exists';
    END IF;
END $$;

-- Add NCBI API key to users table (if not exists)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'ncbi_api_key'
    ) THEN
        ALTER TABLE users ADD COLUMN ncbi_api_key VARCHAR;
        RAISE NOTICE 'Added ncbi_api_key column to users table';
    ELSE
        RAISE NOTICE 'ncbi_api_key column already exists';
    END IF;
END $$;

-- Verify migration
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'users'
ORDER BY ordinal_position;

