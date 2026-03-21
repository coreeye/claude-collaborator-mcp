#!/usr/bin/env python3
"""
claude-collaborator - Multi-AI MCP Server for Large Codebases

Claude (you) + GLM working together to understand complex codebases.
Generic, configurable - works with any C# codebase.
"""

import asyncio
import sys
import traceback
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from claude_collaborator.code_analyzer import CSharpCodeAnalyzer
from claude_collaborator.memory_store import MemoryStore
from claude_collaborator.glm_client import GLMClient
from claude_collaborator.config import load_config
from claude_collaborator.server_middleware import ServerMiddleware
from claude_collaborator.tool_definitions import get_all_tools
from claude_collaborator.tool_handlers import TOOL_HANDLERS, NO_INIT_REQUIRED

# Optional vector memory components
try:
    from claude_collaborator.memory_vector import VectorStore
    from claude_collaborator.memory_auto import AutoCapture
    from claude_collaborator.memory_context import ContextTracker
    from claude_collaborator.memory_cache import FileCache
    from claude_collaborator.memory_session import SessionState
    VECTOR_MEMORY_AVAILABLE = True
except ImportError:
    VECTOR_MEMORY_AVAILABLE = False


class ClaudeCollaboratorServer(ServerMiddleware):
    """MCP Server for multi-AI codebase collaboration"""

    def __init__(self, codebase_path: str = None):
        """Initialize the server with configurable codebase path"""
        # Load configuration
        self.config = load_config()

        # Component placeholders (initialized when codebase is set)
        self.codebase_path = None
        self.memory = None
        self.analyzer = None

        # Vector memory components (initialized when codebase is set, if available)
        self.vector_store = None
        self.auto_capture = None
        self.context_tracker = None
        self.file_cache = None
        self.session_state = None

        # Initialize middleware (auto-capture, GLM enrich, etc.)
        self._init_middleware()

        # Initialize GLM client (optional - independent of codebase)
        try:
            self.glm = GLMClient()
            self.glm_available = True
        except ValueError:
            self.glm = None
            self.glm_available = False

        # Create MCP server
        self.app = Server("claude-collaborator")

        # Register tools
        self._register_tools()

        # Store configured codebase path for lazy initialization
        # Priority: 1) passed argument, 2) config, 3) None (requires switch_codebase)
        self._configured_codebase_path = codebase_path
        if not self._configured_codebase_path:
            config_path = self.config.get("codebase_path")
            if config_path:
                self._configured_codebase_path = str(config_path)

        # DON'T initialize here - will be lazy-loaded in _ensure_codebase()
        # This prevents blocking the MCP server from starting

    def _initialize_codebase(self, path: Path):
        """Initialize analyzer and memory store for a codebase path"""
        if not path.exists():
            raise ValueError(f"Codebase path not found: {path}")

        self.codebase_path = path
        self.memory = MemoryStore(str(path))
        self.analyzer = CSharpCodeAnalyzer(str(path))

        # Initialize vector memory components if available
        if VECTOR_MEMORY_AVAILABLE:
            try:
                self.vector_store = VectorStore(str(path))
                self.auto_capture = AutoCapture(
                    self.vector_store,
                    self.memory,
                    enabled=self.config.get("auto_capture_enabled", True)
                )
                context_threshold = self.config.get("context_threshold", 50000)
                self.context_tracker = ContextTracker(
                    self.vector_store,
                    threshold_chars=context_threshold
                )

                cache_size = self.config.get("cache_size", 100)
                cache_ttl = self.config.get("cache_ttl", 3600)
                self.file_cache = FileCache(
                    self.vector_store,
                    max_entries=cache_size,
                    default_ttl=cache_ttl
                )

                self.session_state = SessionState(str(path))

            except Exception as e:
                print(f"Warning: Vector memory initialization failed: {e}", file=sys.stderr)
                self.vector_store = None
                self.auto_capture = None
                self.context_tracker = None
                self.file_cache = None
                self.session_state = None
        else:
            self.file_cache = None
            self.session_state = None

    def _ensure_codebase(self):
        """
        Ensure codebase is initialized (lazy loading).

        Called on first tool access instead of during __init__
        to prevent blocking the MCP server from starting.
        """
        if self.codebase_path is not None:
            return

        if not self._configured_codebase_path:
            return

        try:
            self._initialize_codebase(Path(self._configured_codebase_path))
        except Exception as e:
            print(f"Warning: Could not initialize codebase: {e}", file=sys.stderr)
            print(f"  Path was: {self._configured_codebase_path}", file=sys.stderr)
            print(f"  Use switch_codebase() to select a codebase manually.", file=sys.stderr)

    def switch_codebase(self, path: str) -> dict:
        """Switch to a different codebase."""
        new_path = Path(path)

        if not new_path.is_absolute():
            new_path = Path.cwd() / new_path

        new_path = new_path.resolve()

        if not new_path.exists():
            return {"success": False, "error": f"Path not found: {new_path}"}

        if not new_path.is_dir():
            return {"success": False, "error": f"Path is not a directory: {new_path}"}

        try:
            self._initialize_codebase(new_path)

            cs_files = list(new_path.rglob("*.cs"))
            projects = list(new_path.rglob("*.csproj"))
            solutions = [s.name for s in new_path.rglob("*.sln")]

            return {
                "success": True,
                "codebase_path": str(new_path),
                "cs_files_count": len(cs_files),
                "projects_count": len(projects),
                "solutions": solutions,
                "memory_path": str(self.memory.memory_path) if self.memory else None
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_codebases(self, search_path: str = None) -> dict:
        """Discover codebases by searching for .sln and .git directories."""
        search_dir = Path(search_path) if search_path else Path.cwd()

        if not search_dir.exists():
            return {"success": False, "error": f"Search path not found: {search_dir}"}

        codebases = []

        # Find .sln files
        try:
            for sln in search_dir.rglob("*.sln"):
                if any(part.startswith('.') for part in sln.parts):
                    continue
                codebases.append({
                    "name": sln.stem,
                    "root": str(sln.parent),
                    "type": "solution",
                    "file": str(sln)
                })
        except PermissionError:
            pass

        # Find .git directories (limit depth)
        try:
            for git_dir in search_dir.rglob(".git"):
                if not git_dir.is_dir():
                    continue
                repo_root = git_dir.parent
                # Skip if already found via .sln
                if not any(cb["root"] == str(repo_root) for cb in codebases):
                    codebases.append({
                        "name": repo_root.name,
                        "root": str(repo_root),
                        "type": "git",
                        "file": str(git_dir)
                    })
        except PermissionError:
            pass

        return {
            "success": True,
            "search_path": str(search_dir),
            "codebases_count": len(codebases),
            "codebases": codebases
        }

    def _check_initialized(self) -> tuple[bool, str]:
        """Check if codebase is initialized. Triggers lazy init if configured."""
        self._ensure_codebase()

        if self.codebase_path is None:
            return False, (
                "No codebase selected. Use `switch_codebase` to select a codebase first.\n"
                "Example: switch_codebase(path=\"C:\\\\path\\\\to\\\\your\\\\project\")\n"
                "Or use `list_codebases` to discover available codebases."
            )
        return True, None

    def _dispatch_tool(self, name: str, arguments: dict) -> list[TextContent]:
        """Synchronous tool dispatch — runs in a thread executor to avoid blocking the event loop."""
        # Start embedding model warmup on first tool call (after MCP transport is up)
        if self.vector_store and not self.vector_store._warmup_started:
            self.vector_store.ensure_warmup_started()

        # Pre-tool: retrieve relevant context
        retrieved_context = self._auto_retrieve_context(name, arguments)
        self._current_retrieved_context = retrieved_context

        # Check if tool requires initialization
        if name not in NO_INIT_REQUIRED:
            is_ready, error_msg = self._check_initialized()
            if not is_ready:
                return self._process_tool_result(name, arguments,
                    [TextContent(type="text", text=error_msg)])

        # Dispatch to handler
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return self._process_tool_result(name, arguments,
                [TextContent(type="text", text=f"Unknown tool: {name}")])

        result_text = handler(self, arguments)

        # Auto-capture for certain tools
        from claude_collaborator.tool_handlers import AUTO_CAPTURE_TOOLS
        if name in AUTO_CAPTURE_TOOLS:
            self._maybe_auto_capture(name, arguments, result_text)

        return self._process_tool_result(name, arguments,
            [TextContent(type="text", text=result_text)])

    def _register_tools(self):
        """Register all MCP tools"""

        @self.app.list_tools()
        async def list_tools() -> list[Tool]:
            return get_all_tools()

        @self.app.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            try:
                # Run the entire tool dispatch in a thread to avoid blocking
                # the async event loop (embedding model loading, vector search,
                # and tool handlers can all block for seconds)
                return await asyncio.get_event_loop().run_in_executor(
                    None, self._dispatch_tool, name, arguments
                )

            except Exception as e:
                return self._process_tool_result(name, arguments,
                    [TextContent(type="text", text=f"Error: {str(e)}\n\nTraceback:\n{traceback.format_exc()}")])

    async def run(self):
        """Run the MCP server"""
        async with stdio_server() as (read_stream, write_stream):
            await self.app.run(
                read_stream,
                write_stream,
                self.app.create_initialization_options()
            )


def main():
    """Main entry point"""
    import io
    import os

    # Protect the MCP stdio transport from stray stdout writes.
    # SentenceTransformer and HuggingFace libraries print progress bars and
    # load reports directly to fd 1 (stdout), corrupting the MCP JSON-RPC
    # stream. We solve this by moving the real stdout to a separate fd
    # BEFORE the MCP transport starts. The transport captures sys.stdout.buffer
    # at init, so it will use the safe fd. Later, the warmup thread can
    # redirect fd 1 to devnull without affecting the transport.
    safe_stdout_fd = os.dup(1)  # Backup real stdout to a new fd
    safe_stdout = os.fdopen(safe_stdout_fd, "wb", closefd=False)
    sys.stdout = io.TextIOWrapper(safe_stdout, encoding="utf-8", line_buffering=True)

    server = ClaudeCollaboratorServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
