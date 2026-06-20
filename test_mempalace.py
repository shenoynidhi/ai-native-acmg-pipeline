#!/usr/bin/env python
"""
Test script for MemPalace functionality.
Run this after completing an analysis to see memory operations in action.
"""

import sys
from src.api.db import SessionLocal, User
from src.mempalace.palace import search_memories, wake_up
from src.mempalace.knowledge_graph import get_gene_variants, get_variant_history, get_recent_analyses

def test_mempalace():
    """Test MemPalace operations."""
    db = SessionLocal()

    # Get first user for testing
    user = db.query(User).first()
    if not user:
        print("❌ No users found. Register a user first.")
        return

    user_id = str(user.user_id)
    print(f"✅ Testing MemPalace for user: {user.email} ({user_id})")
    print("=" * 70)

    # Test 1: Recent analyses
    print("\n📊 Recent Analyses:")
    recent = get_recent_analyses(user_id, limit=5, db=db)
    if recent:
        for analysis in recent:
            print(f"  • Session {analysis['session_id']} on {analysis['date']}")
            print(f"    Variants: {', '.join(analysis['variants'][:3])}")
    else:
        print("  No analyses found yet.")

    # Test 2: Semantic search
    print("\n🔍 Semantic Search - 'BRCA2 pathogenic variants':")
    results = search_memories(
        user_id=user_id,
        query="BRCA2 pathogenic variants",
        limit=3,
        db=db
    )
    if results:
        for result in results:
            print(f"  • [{result['wing']}] {result['content'][:100]}...")
            print(f"    Similarity: {result['similarity']:.3f}")
    else:
        print("  No memories found.")

    # Test 3: Gene variants
    print("\n🧬 All BRCA2 Variants:")
    brca2_variants = get_gene_variants(user_id, "BRCA2", db=db)
    if brca2_variants:
        for variant in brca2_variants:
            print(f"  • {variant['variant_id']}: {variant['current_classification']}")
            print(f"    Seen in sessions: {', '.join(variant['sessions'][:2])}")
    else:
        print("  No BRCA2 variants found.")

    # Test 4: Variant history
    if brca2_variants:
        first_variant = brca2_variants[0]
        print(f"\n📜 Classification History for {first_variant['variant_id']}:")
        history = get_variant_history(
            user_id,
            first_variant['variant_id'],
            "BRCA2",
            db=db
        )
        for record in history:
            status = "✓ Current" if record['is_current'] else "  Past"
            print(f"  {status}: {record['classification']} (from {record['valid_from']})")

    # Test 5: Wake up (context-aware memory retrieval)
    print("\n🧠 Wake Up - Context: 'Analyzing a missense variant with PM2 and PP3':")
    memories = wake_up(
        user_id=user_id,
        context="Analyzing a missense variant with PM2 and PP3 criteria",
        wings=["analysis_history", "variants"],
        limit=3,
        db=db
    )
    if memories:
        for memory in memories:
            print(f"  • [{memory['wing']}] {memory['content'][:80]}...")
            print(f"    Relevance: {memory['similarity']:.3f}")
    else:
        print("  No relevant memories found.")

    print("\n" + "=" * 70)
    print("✅ MemPalace test complete!")

    db.close()


if __name__ == "__main__":
    test_mempalace()

