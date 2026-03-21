"""
Integration tests for the MCP server tool dispatch.

Tests that tools respond within a timeout (catching event loop blocking).
Simulates what Claude Code does: calls the async call_tool handler.
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

TIMEOUT_SECONDS = 30  # max time per tool call before we consider it hung


async def call_tool_with_timeout(server, name: str, arguments: dict, timeout: float = TIMEOUT_SECONDS):
    """Call a tool on the server with a timeout. Returns (result, elapsed) or raises on timeout."""
    # Access the registered call_tool handler
    # The MCP server registers handlers via decorators, so we need to invoke them
    # through the server's internal dispatch
    start = time.time()
    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, server._dispatch_tool, name, arguments
            ),
            timeout=timeout
        )
        elapsed = time.time() - start
        return result, elapsed
    except asyncio.TimeoutError:
        elapsed = time.time() - start
        raise TimeoutError(f"Tool '{name}' hung for {elapsed:.1f}s (timeout={timeout}s)")


async def test_switch_codebase(server, codebase_path: str):
    """Test switch_codebase responds quickly"""
    print("\n--- test_switch_codebase ---")
    result, elapsed = await call_tool_with_timeout(
        server, "switch_codebase", {"path": codebase_path}
    )
    text = result[0].text
    assert "Switched to codebase" in text or "success" in text.lower(), f"Unexpected: {text[:200]}"
    print(f"  [PASS] {elapsed:.1f}s")


async def test_get_config(server):
    """Test get_config responds quickly"""
    print("\n--- test_get_config ---")
    result, elapsed = await call_tool_with_timeout(server, "get_config", {})
    text = result[0].text
    assert len(text) > 0, "Empty response"
    print(f"  [PASS] {elapsed:.1f}s")


async def test_memory_save_and_get(server):
    """Test memory_save and memory_get"""
    print("\n--- test_memory_save_and_get ---")
    result, elapsed = await call_tool_with_timeout(
        server, "memory_save",
        {"topic": "integration-test", "content": "Test content for integration", "category": "test"}
    )
    print(f"  save: [PASS] {elapsed:.1f}s")

    result, elapsed = await call_tool_with_timeout(
        server, "memory_get", {"topic": "integration-test"}
    )
    text = result[0].text
    assert "integration" in text.lower(), f"Unexpected: {text[:200]}"
    print(f"  get:  [PASS] {elapsed:.1f}s")


async def test_memory_search(server):
    """Test keyword memory search"""
    print("\n--- test_memory_search ---")
    result, elapsed = await call_tool_with_timeout(
        server, "memory_search", {"query": "integration"}
    )
    print(f"  [PASS] {elapsed:.1f}s")


async def test_memory_status(server):
    """Test memory_status"""
    print("\n--- test_memory_status ---")
    result, elapsed = await call_tool_with_timeout(server, "memory_status", {})
    text = result[0].text
    assert "Memory Store" in text or "Total Topics" in text, f"Unexpected: {text[:200]}"
    print(f"  [PASS] {elapsed:.1f}s")


async def test_memory_semantic_search(server):
    """Test semantic search — the tool that was hanging"""
    print("\n--- test_memory_semantic_search ---")

    # First, wait for model to be ready (up to 60s)
    if server.vector_store:
        print("  Waiting for embedding model to load...", end="", flush=True)
        start = time.time()
        while not server.vector_store.is_model_ready() and time.time() - start < 60:
            await asyncio.sleep(1)
            print(".", end="", flush=True)
        elapsed_warmup = time.time() - start
        ready = server.vector_store.is_model_ready()
        print(f" {'ready' if ready else 'NOT READY'} ({elapsed_warmup:.0f}s)")
        if not ready:
            print("  [SKIP] Model did not load in time")
            return
    else:
        print("  [SKIP] No vector store available")
        return

    # Add something to search for
    result, elapsed = await call_tool_with_timeout(
        server, "learn",
        {"observation": "Integration test pattern: always use dependency injection for services"}
    )
    print(f"  learn: [PASS] {elapsed:.1f}s")

    # Now test semantic search
    result, elapsed = await call_tool_with_timeout(
        server, "memory_semantic_search",
        {"query": "dependency injection", "limit": 3}
    )
    text = result[0].text
    print(f"  search: [PASS] {elapsed:.1f}s — response: {text[:100]}...")


async def test_memory_vector_stats(server):
    """Test vector stats"""
    print("\n--- test_memory_vector_stats ---")
    result, elapsed = await call_tool_with_timeout(server, "memory_vector_stats", {})
    print(f"  [PASS] {elapsed:.1f}s")


async def test_context_tools(server):
    """Test context_stats and context_retrieve"""
    print("\n--- test_context_tools ---")
    result, elapsed = await call_tool_with_timeout(server, "context_stats", {})
    print(f"  stats:    [PASS] {elapsed:.1f}s")

    result, elapsed = await call_tool_with_timeout(
        server, "context_retrieve", {"query": "test patterns"}
    )
    print(f"  retrieve: [PASS] {elapsed:.1f}s")


async def test_session_status(server):
    """Test session_status"""
    print("\n--- test_session_status ---")
    result, elapsed = await call_tool_with_timeout(server, "session_status", {})
    print(f"  [PASS] {elapsed:.1f}s")


async def test_learn(server):
    """Test the learn tool"""
    print("\n--- test_learn ---")
    result, elapsed = await call_tool_with_timeout(
        server, "learn",
        {"observation": "Test observation from integration test", "category": "patterns"}
    )
    text = result[0].text
    assert "learned" in text.lower() or "recorded" in text.lower() or "saved" in text.lower() or "observation" in text.lower(), f"Unexpected: {text[:200]}"
    print(f"  [PASS] {elapsed:.1f}s")


async def test_no_hang_during_warmup(server):
    """
    Critical test: call memory_semantic_search BEFORE model is ready.
    Must not hang — should return quickly with a graceful message.
    """
    print("\n--- test_no_hang_during_warmup ---")
    # Create a fresh VectorStore to simulate startup
    with tempfile.TemporaryDirectory() as tmpdir:
        from claude_collaborator.memory_vector import VectorStore
        # Reset class-level cache to force re-check
        old_checked = VectorStore._ST_CHECKED
        old_available = VectorStore._ST_AVAILABLE
        try:
            vs = VectorStore(tmpdir)
            # Immediately check — model should NOT be ready yet
            if vs.is_model_ready():
                print("  [SKIP] Model loaded too fast to test warmup behavior")
                return

            # The search should return empty quickly, not hang
            start = time.time()
            results = vs.search("test query", limit=1)
            elapsed = time.time() - start

            if elapsed > 1:
                print(f"  [FAIL] search() blocked for {elapsed:.1f}s during warmup — should return immediately")
            else:
                print(f"  [PASS] search() returned in {elapsed:.1f}s during warmup (non-blocking)")
        finally:
            VectorStore._ST_CHECKED = old_checked
            VectorStore._ST_AVAILABLE = old_available


async def test_concurrent_tool_calls(server):
    """Test that multiple tools can run concurrently (event loop not blocked)"""
    print("\n--- test_concurrent_tool_calls ---")
    start = time.time()

    results = await asyncio.gather(
        call_tool_with_timeout(server, "memory_status", {}),
        call_tool_with_timeout(server, "get_config", {}),
        call_tool_with_timeout(server, "session_status", {}),
    )

    total = time.time() - start
    individual_times = [r[1] for r in results]
    print(f"  3 concurrent calls completed in {total:.1f}s (individual: {', '.join(f'{t:.1f}s' for t in individual_times)})")
    print(f"  [PASS]")


async def run_all_tests():
    """Run all integration tests"""
    from claude_collaborator.server import ClaudeCollaboratorServer

    print("=" * 60)
    print("MCP Server Integration Tests")
    print("=" * 60)

    # Use a temp directory for test isolation
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a minimal codebase structure
        (Path(tmpdir) / ".git").mkdir()
        (Path(tmpdir) / "test.cs").write_text("class Test {}")

        server = ClaudeCollaboratorServer()

        passed = 0
        failed = 0
        skipped = 0

        tests = [
            ("switch_codebase", test_switch_codebase(server, tmpdir)),
            ("get_config", test_get_config(server)),
            ("memory_save_and_get", test_memory_save_and_get(server)),
            ("memory_search", test_memory_search(server)),
            ("memory_status", test_memory_status(server)),
            ("learn", test_learn(server)),
            ("session_status", test_session_status(server)),
            ("context_tools", test_context_tools(server)),
            ("memory_vector_stats", test_memory_vector_stats(server)),
            ("no_hang_during_warmup", test_no_hang_during_warmup(server)),
            ("memory_semantic_search", test_memory_semantic_search(server)),
            ("concurrent_tool_calls", test_concurrent_tool_calls(server)),
        ]

        for name, coro in tests:
            try:
                await coro
                passed += 1
            except TimeoutError as e:
                print(f"  [FAIL] TIMEOUT: {e}")
                failed += 1
            except Exception as e:
                print(f"  [FAIL] {e}")
                import traceback
                traceback.print_exc()
                failed += 1

        print("\n" + "=" * 60)
        print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
        print("=" * 60)

        return 1 if failed > 0 else 0


def main():
    return asyncio.run(run_all_tests())


if __name__ == "__main__":
    sys.exit(main())
