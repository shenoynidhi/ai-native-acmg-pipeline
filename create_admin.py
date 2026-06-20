#!/usr/bin/env python
"""
create_admin.py

Script to create the first admin user for the ACMG Pipeline.
Run this once to set up administrative access.

Usage:
    python create_admin.py

The script will prompt for admin details and generate an API key.
SAVE THE API KEY - it's shown only once!
"""

import secrets
import bcrypt
from src.api.db import SessionLocal, User

def create_admin_user():
    """Create an admin user with elevated privileges."""

    print("=" * 70)
    print("ACMG Pipeline - Create Admin User")
    print("=" * 70)
    print()

    # Get admin details
    email = input("Admin email: ").strip()
    if not email:
        print("Error: Email is required")
        return

    name = input("Admin name: ").strip()
    if not name:
        print("Error: Name is required")
        return

    organisation = input("Organisation (optional): ").strip() or "System Administration"

    print()
    print("Creating admin user...")

    # Create database session
    db = SessionLocal()

    try:
        # Check if email already exists
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"Error: User with email {email} already exists")

            # Offer to make them admin
            make_admin = input("Make this user an admin? (yes/no): ").strip().lower()
            if make_admin == "yes":
                existing.is_admin = True
                db.commit()
                print(f"✓ User {email} is now an admin!")
            return

        # Generate admin API key
        admin_api_key = secrets.token_urlsafe(32)
        api_key_hash = bcrypt.hashpw(
            admin_api_key.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

        # Create admin user
        admin = User(
            email=email,
            name=name,
            organisation=organisation,
            api_key_hash=api_key_hash,
            max_analyses=99999,  # High quota for admin
            analyses_used=0,
            is_admin=True,
            is_active=True,
        )

        db.add(admin)
        db.commit()
        db.refresh(admin)

        print()
        print("=" * 70)
        print("✓ Admin user created successfully!")
        print("=" * 70)
        print()
        print(f"User ID:      {admin.user_id}")
        print(f"Email:        {email}")
        print(f"Name:         {name}")
        print(f"Organisation: {organisation}")
        print(f"Is Admin:     Yes")
        print(f"Max Analyses: {admin.max_analyses}")
        print()
        print("=" * 70)
        print("ADMIN API KEY (save this now!):")
        print("=" * 70)
        print()
        print(f"    {admin_api_key}")
        print()
        print("=" * 70)
        print("⚠️  WARNING: This key won't be shown again!")
        print("=" * 70)
        print()
        print("Usage:")
        print(f'    export ADMIN_KEY="{admin_api_key}"')
        print('    curl http://localhost:8000/admin/users -H "X-API-Key: $ADMIN_KEY"')
        print()

    except Exception as e:
        print(f"Error creating admin user: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    create_admin_user()

