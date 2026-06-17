"""
src/mempalace/palace.py

Core MemPalace operations for semantic memory storage and retrieval.
Uses sentence-transformers for embeddings and pgvector for similarity search.
"""

import uuid
from typing import List, Optional, Dict
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session
from sentence_transformers import SentenceTransformer

from src.api.db import SessionLocal, PalaceMemory, User

# Load embedding model once at module level
# sentence-transformers/all-MiniLM-L6-v2: 384 dimensions, fast, good quality
_embedding_model = None

def _get_embedding_model():
    """Lazy-load embedding model."""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
    return _embedding_model


def _generate_embedding(text: str) -> List[float]:
    """
    Generate embedding vector for text.

    Args:
        text: Input text to embed

    Returns:
        List of 384 floats (embedding vector)
    """
    model = _get_embedding_model()
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


# ---------------------------------------------------------------------------
# Core Memory Operations
# ---------------------------------------------------------------------------

def mine_memory(
    user_id: str,
    wing: str,
    room: str,
    content: str,
    db: Optional[Session] = None
) -> str:
    """
    Store a new memory in the palace.

    Args:
        user_id: UUID of the user
        wing: High-level category ("analysis_history", "preferences", "variants")
        room: Specific context (gene name, session_id, preference category)
        content: The actual memory content
        db: Database session (creates new one if not provided)

    Returns:
        Memory ID (UUID string)

    Example:
        memory_id = mine_memory(
            user_id="123e4567-...",
            wing="analysis_history",
            room="session_abc123",
            content="Analyzed 3 variants in BRCA2, found 1 Likely Pathogenic"
        )
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # Generate embedding
        embedding = _generate_embedding(content)

        # Create memory record
        memory = PalaceMemory(
            user_id=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
            wing=wing,
            room=room,
            content=content,
            embedding=embedding,
            is_deleted=False
        )

        db.add(memory)
        db.commit()
        db.refresh(memory)

        return str(memory.id)

    finally:
        if close_db:
            db.close()


def search_memories(
    user_id: str,
    query: str,
    wing: Optional[str] = None,
    room: Optional[str] = None,
    limit: int = 10,
    db: Optional[Session] = None
) -> List[Dict]:
    """
    Semantic search of memories using vector similarity.

    Args:
        user_id: UUID of the user
        query: Search query text
        wing: Optional wing filter
        room: Optional room filter
        limit: Max results to return
        db: Database session

    Returns:
        List of memory dicts with content, similarity score, metadata

    Example:
        results = search_memories(
            user_id="123e4567-...",
            query="BRCA2 pathogenic variants",
            wing="variants",
            limit=5
        )
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # Generate query embedding
        query_embedding = _generate_embedding(query)

        # Build SQL query with pgvector cosine similarity
        sql = text("""
            SELECT
                id,
                wing,
                room,
                content,
                created_at,
                1 - (embedding <=> :query_embedding) as similarity
            FROM palace_memories
            WHERE user_id = :user_id
                AND is_deleted = FALSE
                AND (:wing IS NULL OR wing = :wing)
                AND (:room IS NULL OR room = :room)
            ORDER BY embedding <=> :query_embedding
            LIMIT :limit
        """)

        result = db.execute(sql, {
            'query_embedding': str(query_embedding),
            'user_id': user_id,
            'wing': wing,
            'room': room,
            'limit': limit
        })

        memories = []
        for row in result:
            memories.append({
                'id': str(row.id),
                'wing': row.wing,
                'room': row.room,
                'content': row.content,
                'created_at': row.created_at.isoformat(),
                'similarity': float(row.similarity)
            })

        return memories

    finally:
        if close_db:
            db.close()


def wake_up(
    user_id: str,
    context: str,
    wings: Optional[List[str]] = None,
    limit: int = 20,
    db: Optional[Session] = None
) -> List[Dict]:
    """
    Load relevant memories into context based on current task.

    This is the "wake up" operation - retrieves memories that are semantically
    relevant to the current context to inform decision-making.

    Args:
        user_id: UUID of the user
        context: Current context description (e.g., "analyzing BRCA2 variant")
        wings: Optional list of wings to search (default: all)
        limit: Max memories to retrieve
        db: Database session

    Returns:
        List of relevant memories sorted by relevance

    Example:
        memories = wake_up(
            user_id="123e4567-...",
            context="User is analyzing a BRCA2 missense variant with PM2 and PP3",
            wings=["analysis_history", "variants"],
            limit=10
        )
    """
    # If specific wings provided, search each and merge results
    if wings:
        all_memories = []
        per_wing_limit = max(1, limit // len(wings))

        for wing in wings:
            wing_memories = search_memories(
                user_id=user_id,
                query=context,
                wing=wing,
                limit=per_wing_limit,
                db=db
            )
            all_memories.extend(wing_memories)

        # Sort by similarity and take top N
        all_memories.sort(key=lambda m: m['similarity'], reverse=True)
        return all_memories[:limit]

    # Otherwise search all wings
    return search_memories(
        user_id=user_id,
        query=context,
        limit=limit,
        db=db
    )


def delete_memory(
    memory_id: str,
    db: Optional[Session] = None
) -> bool:
    """
    Soft-delete a memory (marks as deleted, doesn't actually remove).

    Args:
        memory_id: UUID of the memory
        db: Database session

    Returns:
        True if deleted, False if not found
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        memory = db.query(PalaceMemory).filter(
            PalaceMemory.id == uuid.UUID(memory_id)
        ).first()

        if not memory:
            return False

        memory.is_deleted = True
        db.commit()
        return True

    finally:
        if close_db:
            db.close()


def update_memory(
    memory_id: str,
    new_content: str,
    db: Optional[Session] = None
) -> bool:
    """
    Update memory content and regenerate embedding.

    Args:
        memory_id: UUID of the memory
        new_content: New content text
        db: Database session

    Returns:
        True if updated, False if not found
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        memory = db.query(PalaceMemory).filter(
            PalaceMemory.id == uuid.UUID(memory_id)
        ).first()

        if not memory or memory.is_deleted:
            return False

        # Update content and regenerate embedding
        memory.content = new_content
        memory.embedding = _generate_embedding(new_content)

        db.commit()
        return True

    finally:
        if close_db:
            db.close()


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------

def mine_session_summary(
    user_id: str,
    session_id: str,
    variant_count: int,
    classifications: Dict[str, str],
    genome_build: str,
    clinical_notes: str,
    db: Optional[Session] = None
) -> str:
    """
    Store a session summary in analysis_history wing.

    Args:
        user_id: User UUID
        session_id: Session ID
        variant_count: Number of variants processed
        classifications: Dict of {variant_id: classification}
        genome_build: GRCh37 or GRCh38
        clinical_notes: Clinical history provided

    Returns:
        Memory ID
    """
    # Build summary text
    classification_counts = {}
    for cls in classifications.values():
        classification_counts[cls] = classification_counts.get(cls, 0) + 1

    summary_parts = [
        f"Session {session_id}: Analyzed {variant_count} variants on {genome_build}."
    ]

    if classification_counts:
        counts_str = ", ".join([f"{count} {cls}" for cls, count in classification_counts.items()])
        summary_parts.append(f"Results: {counts_str}.")

    if clinical_notes:
        summary_parts.append(f"Clinical context: {clinical_notes[:200]}")

    summary = " ".join(summary_parts)

    return mine_memory(
        user_id=user_id,
        wing="analysis_history",
        room=session_id,
        content=summary,
        db=db
    )

