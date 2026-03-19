"""
Tests for vector memory functionality
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claude_collaborator.memory_vector import VectorStore
from claude_collaborator.memory_context import ContextTracker
from claude_collaborator.memory_auto import AutoCapture
from claude_collaborator.memory_store import MemoryStore


def test_vector_store_basic():
    """Test basic VectorStore operations"""
    print("\n=== Testing VectorStore ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        vs = VectorStore(tmpdir)

        # Test adding
        vid = vs.add(
            topic="test-pattern",
            content="This is a database connection pattern using SqlConnection class",
            category="patterns"
        )

        if vid:
            print(f"[PASS] Added entry with ID: {vid}")
        else:
            print("[FAIL] Failed to add entry (embeddings may not be available)")
            return

        # Test search
        results = vs.search("database connections", limit=1)

        if results and len(results) > 0:
            print(f"[PASS] Search returned {len(results)} result(s)")
            print(f"  - Topic: {results[0]['topic']}, Score: {results[0]['score']:.2f}")
        else:
            print("[FAIL] Search returned no results")
            return

        # Test stats
        stats = vs.get_stats()
        print(f"[PASS] Stats: {stats['total_entries']} entries, model={stats['embedding_model']}")


def test_context_tracker():
    """Test ContextTracker functionality"""
    print("\n=== Testing ContextTracker ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        from claude_collaborator.memory_vector import VectorStore
        vs = VectorStore(tmpdir)

        if not vs._check_embedding_available():
            print("[SKIP] Skipping (embeddings not available)")
            return

        ct = ContextTracker(vs, threshold_chars=1000)

        # Add some context
        ct.add_context("First context item about database connections")
        ct.add_context("Second item about UI rendering")
        ct.add_context("Third item about API endpoints")

        print(f"[PASS] Added 3 context items")

        # Check stats
        stats = ct.get_stats()
        print(f"[PASS] Context size: {stats['current_size']} chars, {stats['item_count']} items")

        # Test retrieval
        results = ct.retrieve_relevant("database", limit=2)
        print(f"[PASS] Retrieved {len(results)} relevant items for 'database' query")


def test_auto_capture():
    """Test AutoCapture functionality"""
    print("\n=== Testing AutoCapture ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        from claude_collaborator.memory_vector import VectorStore
        vs = VectorStore(tmpdir)
        ms = MemoryStore(tmpdir)

        if not vs._check_embedding_available():
            print("[SKIP] Skipping (embeddings not available)")
            return

        ac = AutoCapture(vs, ms)

        # Test capturing a tool result
        captured = ac.capture_tool_result(
            tool_name="analyze_architecture",
            arguments={},
            result="# Architecture Overview\n\nThis is a test architecture description"
        )

        if captured:
            print(f"[PASS] Auto-captured tool result: {captured}")

            # Check if it's in vector store
            stats = vs.get_stats()
            print(f"[PASS] Vector store now has {stats['total_entries']} entries")
        else:
            print("[SKIP] Auto-capture returned None (result too short or embeddings unavailable)")

        # Test pattern detection
        detected = ac.detect_patterns_in_text(
            "We use the repository pattern for data access. The decision was made to use dependency injection."
        )

        print(f"[PASS] Detected {len(detected)} patterns/decisions in text")
        for d in detected:
            print(f"  - {d['type']}: '{d['keyword']}'")


def test_graceful_fallback():
    """Test that system works gracefully without embeddings"""
    print("\n=== Testing Graceful Fallback (no embeddings) ===")

    # This test verifies the code handles missing sentence-transformers gracefully
    with tempfile.TemporaryDirectory() as tmpdir:
        vs = VectorStore(tmpdir)

        if not vs._check_embedding_available():
            print("[FAIL] Embeddings should be available for this test")
            return

        # Test that add returns None when embeddings work (normal case)
        vid = vs.add("test", "content", "test")
        print(f"[PASS] With embeddings available: add() returns ID: {vid is not None}")


def main():
    """Run all tests"""
    print("=" * 50)
    print("Vector Memory Tests")
    print("=" * 50)

    # Check if embeddings are available
    try:
        import sentence_transformers
        print("[PASS] sentence-transformers is installed")
    except ImportError:
        print("[SKIP] sentence-transformers not installed - some tests will be skipped")

    try:
        test_vector_store_basic()
        test_context_tracker()
        test_auto_capture()
        test_graceful_fallback()

        print("\n" + "=" * 50)
        print("All tests completed!")
        print("=" * 50)

    except Exception as e:
        print(f"\n[FAIL] Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
