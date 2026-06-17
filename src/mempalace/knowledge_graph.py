"""
src/mempalace/knowledge_graph.py

Knowledge graph for tracking variant/gene relationships over time.
Records how classifications change and links sessions to variants.
"""

import uuid
from typing import List, Optional, Dict
from datetime import datetime, date
from sqlalchemy.orm import Session

from src.api.db import SessionLocal, PalaceKnowledge


def record_classification(
    user_id: str,
    variant_id: str,
    gene: str,
    classification: str,
    session_id: str,
    db: Optional[Session] = None
) -> str:
    """
    Record a variant classification in the knowledge graph.

    Args:
        user_id: User UUID
        variant_id: Variant identifier (e.g., "13:32338080:A:C")
        gene: Gene symbol
        classification: ACMG classification (P, LP, VUS, LB, B)
        session_id: Session where this was classified
        db: Database session

    Returns:
        Knowledge graph entry ID
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # Create knowledge entries
        entries = []

        # Entry 1: Variant -> classification
        entries.append(PalaceKnowledge(
            user_id=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
            subject=f"{gene}:{variant_id}",
            relation="classified_as",
            object=classification,
            valid_from=date.today(),
            valid_until=None  # Active until reclassified
        ))

        # Entry 2: Variant -> session
        entries.append(PalaceKnowledge(
            user_id=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
            subject=f"{gene}:{variant_id}",
            relation="seen_in_session",
            object=session_id,
            valid_from=date.today(),
            valid_until=None
        ))

        # Entry 3: Gene -> variant
        entries.append(PalaceKnowledge(
            user_id=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
            subject=gene,
            relation="has_variant",
            object=variant_id,
            valid_from=date.today(),
            valid_until=None
        ))

        db.add_all(entries)
        db.commit()

        return str(entries[0].id)

    finally:
        if close_db:
            db.close()


def track_reclassification(
    user_id: str,
    variant_id: str,
    gene: str,
    old_classification: str,
    new_classification: str,
    session_id: str,
    db: Optional[Session] = None
) -> str:
    """
    Track when a variant's classification changes.

    Args:
        user_id: User UUID
        variant_id: Variant identifier
        gene: Gene symbol
        old_classification: Previous classification
        new_classification: New classification
        session_id: Session where reclassification occurred
        db: Database session

    Returns:
        Knowledge graph entry ID
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        subject = f"{gene}:{variant_id}"

        # Mark old classification as expired
        old_entries = db.query(PalaceKnowledge).filter(
            PalaceKnowledge.user_id == uuid.UUID(user_id),
            PalaceKnowledge.subject == subject,
            PalaceKnowledge.relation == "classified_as",
            PalaceKnowledge.object == old_classification,
            PalaceKnowledge.valid_until == None
        ).all()

        for entry in old_entries:
            entry.valid_until = date.today()

        # Record new classification
        new_entry = PalaceKnowledge(
            user_id=uuid.UUID(user_id),
            subject=subject,
            relation="classified_as",
            object=new_classification,
            valid_from=date.today(),
            valid_until=None
        )

        # Record the reclassification event
        reclass_entry = PalaceKnowledge(
            user_id=uuid.UUID(user_id),
            subject=subject,
            relation="reclassified",
            object=f"{old_classification}->{new_classification} in {session_id}",
            valid_from=date.today(),
            valid_until=None
        )

        db.add(new_entry)
        db.add(reclass_entry)
        db.commit()

        return str(new_entry.id)

    finally:
        if close_db:
            db.close()


def get_variant_history(
    user_id: str,
    variant_id: str,
    gene: str,
    db: Optional[Session] = None
) -> List[Dict]:
    """
    Get classification history for a specific variant.

    Args:
        user_id: User UUID
        variant_id: Variant identifier
        gene: Gene symbol
        db: Database session

    Returns:
        List of classification records ordered by date
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        subject = f"{gene}:{variant_id}"

        entries = db.query(PalaceKnowledge).filter(
            PalaceKnowledge.user_id == uuid.UUID(user_id),
            PalaceKnowledge.subject == subject,
            PalaceKnowledge.relation == "classified_as"
        ).order_by(PalaceKnowledge.valid_from.desc()).all()

        history = []
        for entry in entries:
            history.append({
                'classification': entry.object,
                'valid_from': entry.valid_from.isoformat(),
                'valid_until': entry.valid_until.isoformat() if entry.valid_until else None,
                'is_current': entry.valid_until is None
            })

        return history

    finally:
        if close_db:
            db.close()


def get_gene_variants(
    user_id: str,
    gene: str,
    db: Optional[Session] = None
) -> List[Dict]:
    """
    Get all variants in a gene that this user has analyzed.

    Args:
        user_id: User UUID
        gene: Gene symbol
        db: Database session

    Returns:
        List of variant records with their current classifications
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # Get all variants for this gene
        variant_entries = db.query(PalaceKnowledge).filter(
            PalaceKnowledge.user_id == uuid.UUID(user_id),
            PalaceKnowledge.subject == gene,
            PalaceKnowledge.relation == "has_variant",
            PalaceKnowledge.valid_until == None
        ).all()

        variants = []
        for entry in variant_entries:
            variant_id = entry.object

            # Get current classification
            classification_entry = db.query(PalaceKnowledge).filter(
                PalaceKnowledge.user_id == uuid.UUID(user_id),
                PalaceKnowledge.subject == f"{gene}:{variant_id}",
                PalaceKnowledge.relation == "classified_as",
                PalaceKnowledge.valid_until == None
            ).first()

            # Get sessions where this variant was seen
            session_entries = db.query(PalaceKnowledge).filter(
                PalaceKnowledge.user_id == uuid.UUID(user_id),
                PalaceKnowledge.subject == f"{gene}:{variant_id}",
                PalaceKnowledge.relation == "seen_in_session"
            ).all()

            variants.append({
                'variant_id': variant_id,
                'gene': gene,
                'current_classification': classification_entry.object if classification_entry else None,
                'sessions': [e.object for e in session_entries],
                'first_seen': entry.valid_from.isoformat()
            })

        return variants

    finally:
        if close_db:
            db.close()


def get_recent_analyses(
    user_id: str,
    limit: int = 10,
    db: Optional[Session] = None
) -> List[Dict]:
    """
    Get user's most recent variant analyses.

    Args:
        user_id: User UUID
        limit: Max results
        db: Database session

    Returns:
        List of recent analyses
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # Get recent session associations
        entries = db.query(PalaceKnowledge).filter(
            PalaceKnowledge.user_id == uuid.UUID(user_id),
            PalaceKnowledge.relation == "seen_in_session"
        ).order_by(
            PalaceKnowledge.valid_from.desc()
        ).limit(limit * 3).all()  # Get extra to account for duplicates

        # Deduplicate by session and collect variants
        sessions = {}
        for entry in entries:
            session_id = entry.object
            variant = entry.subject

            if session_id not in sessions:
                sessions[session_id] = {
                    'session_id': session_id,
                    'date': entry.valid_from.isoformat(),
                    'variants': []
                }

            sessions[session_id]['variants'].append(variant)

        # Return most recent sessions
        recent = list(sessions.values())[:limit]
        return recent

    finally:
        if close_db:
            db.close()

