"""
src/api/auth.py

Authentication and authorization logic for the ACMG Pipeline API.
Handles user registration, API key validation, and quota enforcement.
"""

import secrets
import bcrypt
from typing import Optional
from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from src.api.db import get_db, User
from src.api.models import RegisterRequest, RegisterResponse


# ---------------------------------------------------------------------------
# User Registration
# ---------------------------------------------------------------------------

def register_user(request: RegisterRequest, db: Session) -> RegisterResponse:
    """
    Create a new user account and issue an API key.

    Args:
        request: Registration details (email, name, organisation)
        db: Database session

    Returns:
        RegisterResponse with user_id and plaintext API key (shown only once)

    Raises:
        HTTPException 400 if email already exists
    """
    # Check if email already registered
    existing = db.query(User).filter(User.email == request.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Generate API key (UUID format, cryptographically secure)
    api_key = secrets.token_urlsafe(32)  # 256-bit entropy

    # Hash the API key with bcrypt
    api_key_hash = bcrypt.hashpw(api_key.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    # Create user record
    user = User(
        email=request.email,
        name=request.name,
        organisation=request.organisation,
        api_key_hash=api_key_hash,
        max_analyses=100,  # Default quota
        analyses_used=0,
        is_active=True
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return RegisterResponse(
        user_id=str(user.user_id),
        api_key=api_key,  # Plain text - ONLY TIME it's shown
        message="Account created successfully. Save your API key - it won't be shown again!"
    )


# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------

def verify_api_key(
    x_api_key: str = Header(..., description="API key for authentication"),
    db: Session = Depends(get_db)
) -> User:
    """
    Verify API key and return authenticated user.

    This is a FastAPI dependency - use it in endpoint signatures:
        @app.get("/endpoint")
        def endpoint(user: User = Depends(verify_api_key)):
            ...

    Args:
        x_api_key: API key from X-API-Key header
        db: Database session (injected)

    Returns:
        User object if authentication succeeds

    Raises:
        HTTPException 401 if key invalid
        HTTPException 403 if user inactive
        HTTPException 429 if quota exceeded
    """
    # Get all active users (we need to check hash against all)
    users = db.query(User).filter(User.is_active == True).all()

    authenticated_user = None
    for user in users:
        # Check if provided key matches this user's hash
        if bcrypt.checkpw(x_api_key.encode('utf-8'), user.api_key_hash.encode('utf-8')):
            authenticated_user = user
            break

    if not authenticated_user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not authenticated_user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive")

    # Check quota
    if authenticated_user.analyses_used >= authenticated_user.max_analyses:
        raise HTTPException(
            status_code=429,
            detail=f"Analysis quota exceeded ({authenticated_user.max_analyses} analyses used)"
        )

    return authenticated_user


def increment_usage(user: User, db: Session):
    """
    Increment the user's analysis usage counter.
    Call this after successfully queuing an analysis job.

    Args:
        user: Authenticated user
        db: Database session
    """
    user.analyses_used += 1
    db.commit()


# ---------------------------------------------------------------------------
# Optional API Key (for public endpoints that work better with auth)
# ---------------------------------------------------------------------------

def optional_api_key(
    x_api_key: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Optional authentication - returns User if key provided, None otherwise.

    Use for endpoints that work without auth but provide extra features when authenticated.

    Args:
        x_api_key: Optional API key from X-API-Key header
        db: Database session (injected)

    Returns:
        User object if valid key provided, None otherwise
    """
    if not x_api_key:
        return None

    try:
        return verify_api_key(x_api_key=x_api_key, db=db)
    except HTTPException:
        return None

