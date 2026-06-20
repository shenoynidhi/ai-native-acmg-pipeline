#!/usr/bin/env python
"""
migrate_database.py

Python script to migrate database schema - adds new columns to existing tables.

This is safer than SQL because it checks if columns exist before adding them.
Run this after pulling new code to update your database schema.

Usage:
    python migrate_database.py
"""

import os
from sqlalchemy import text, inspect
from src.api.db import engine, SessionLocal

def migrate():
    """Add new columns to existing database tables."""
    print("=" * 70)
    print("ACMG Pipeline - Database Migration")
    print("=" * 70)
    print()

    db = SessionLocal()
    inspector = inspect(engine)

    try:
        # Check if users table exists
        if 'users' not in inspector.get_table_names():
            print("❌ Users table doesn't exist. Run: python src/api/db.py first")
            return

        # Get existing columns
        existing_columns = {col['name'] for col in inspector.get_columns('users')}

        print(f"Current columns in users table: {', '.join(existing_columns)}")
        print()

        changes_made = False

        # Add is_admin column
        if 'is_admin' not in existing_columns:
            print("Adding is_admin column...")
            db.execute(text("""
                ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE
            """))
            db.commit()
            print("✓ Added is_admin column")
            changes_made = True
        else:
            print("✓ is_admin column already exists")

        # Add ncbi_api_key column
        if 'ncbi_api_key' not in existing_columns:
            print("Adding ncbi_api_key column...")
            db.execute(text("""
                ALTER TABLE users ADD COLUMN ncbi_api_key VARCHAR
            """))
            db.commit()
            print("✓ Added ncbi_api_key column")
            changes_made = True
        else:
            print("✓ ncbi_api_key column already exists")

        print()
        if changes_made:
            print("=" * 70)
            print("✓ Migration complete - database schema updated!")
            print("=" * 70)
        else:
            print("=" * 70)
            print("✓ No changes needed - database is up to date")
            print("=" * 70)

        # Show final schema
        print()
        print("Current users table schema:")
        for col in inspector.get_columns('users'):
            nullable = "NULL" if col['nullable'] else "NOT NULL"
            default = f"DEFAULT {col['default']}" if col['default'] else ""
            print(f"  - {col['name']:20} {str(col['type']):30} {nullable:10} {default}")

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    migrate()

