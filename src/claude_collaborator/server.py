#!/usr/bin/env python3
"""
claude-collaborator - Multi-AI MCP Server for Large Codebases

Claude (you) + GLM working together to understand complex codebases.
Generic, configurable - works with any C# codebase.
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime
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

# Optional vector memory components
try:
    from claude_collaborator.memory_vector import VectorStore
    from claude_collaborator.memory_auto import AutoCapture
    from claude_collaborator.memory_context import ContextTracker
    VECTOR_MEMORY_AVAILABLE = True
except ImportError:
    VECTOR_MEMORY_AVAILABLE = False


class ClaudeCollaboratorServer:
    """MCP Server for multi-AI codebase collaboration"""

    # Context limits to avoid GLM API errors
    MAX_GLM_CONTEXT = 10000  # characters
    MAX_CODE_LINES = 500     # lines to send to GLM
    MAX_MEMORY_RESULTS = 3    # number of memory results to include

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

        # Initialize codebase ONLY if explicitly provided
        # Do NOT auto-detect from config - user must call switch_codebase
        if codebase_path:
            self.switch_codebase(codebase_path)
        # Otherwise start uninitialized - user must call switch_codebase

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
            except Exception as e:
                # Graceful fallback if vector memory fails to initialize
                print(f"Warning: Vector memory initialization failed: {e}")
                self.vector_store = None
                self.auto_capture = None
                self.context_tracker = None

    def switch_codebase(self, path: str) -> dict:
        """
        Switch to a different codebase.

        Args:
            path: Path to the new codebase root

        Returns:
            dict with status and info about the new codebase
        """
        new_path = Path(path)

        # If relative, resolve from current working directory
        if not new_path.is_absolute():
            new_path = Path.cwd() / new_path

        # Validate path exists
        if not new_path.exists():
            return {
                "success": False,
                "error": f"Path not found: {new_path}"
            }

        # Reinitialize components
        self._initialize_codebase(new_path)

        # Gather info about the new codebase
        cs_files = list(new_path.rglob("*.cs"))
        sln_files = list(new_path.glob("*.sln"))
        csproj_files = list(new_path.rglob("*.csproj"))

        return {
            "success": True,
            "codebase_path": str(new_path),
            "cs_files_count": len(cs_files),
            "solutions": [f.name for f in sln_files],
            "projects_count": len(csproj_files),
            "memory_path": str(self.memory.memory_path)
        }

    def list_codebases(self, search_path: str = None) -> dict:
        """
        Search for codebases in a directory.

        Args:
            search_path: Directory to search (default: current working directory)

        Returns:
            dict with list of discovered codebases
        """
        if search_path:
            search_dir = Path(search_path)
        else:
            search_dir = Path.cwd()

        if not search_dir.exists():
            return {"success": False, "error": f"Search path not found: {search_dir}"}

        codebases = []

        # Search for .sln files (Visual Studio solutions)
        for sln in search_dir.rglob("*.sln"):
            root = sln.parent
            codebases.append({
                "type": "solution",
                "name": sln.stem,
                "root": str(root),
                "file": str(sln)
            })

        # Search for .git directories (git repos)
        for git in search_dir.rglob(".git"):
            if git.is_dir():
                root = git.parent
                # Skip if we already have this root from a .sln
                if not any(cb["root"] == str(root) for cb in codebases):
                    codebases.append({
                        "type": "git",
                        "name": root.name,
                        "root": str(root),
                        "file": str(git)
                    })

        return {
            "success": True,
            "search_path": str(search_dir),
            "codebases_count": len(codebases),
            "codebases": codebases
        }

    def _check_initialized(self) -> tuple[bool, str]:
        """Check if codebase is initialized. Returns (is_ready, error_message)"""
        if self.codebase_path is None:
            return False, (
                "No codebase selected. Use `switch_codebase` to select a codebase first.\n"
                "Example: switch_codebase(path=\"C:\\\\path\\\\to\\\\your\\\\project\")\n"
                "Or use `list_codebases` to discover available codebases."
            )
        return True, None

    def _truncate_for_glm(self, content: str, max_chars: int = None) -> str:
        """Truncate content to avoid GLM context errors"""
        limit = max_chars or self.MAX_GLM_CONTEXT
        if len(content) > limit:
            return content[:limit] + "\n\n... [truncated]"
        return content

    def _maybe_auto_capture(self, tool_name: str, arguments: dict, result: str) -> Optional[str]:
        """Attempt auto-capture of tool result if available and significant"""
        if self.auto_capture:
            captured_id = self.auto_capture.capture_tool_result(tool_name, arguments, result)
            return captured_id
        return None

    def _register_tools(self):
        """Register all MCP tools"""

        @self.app.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools"""
            tools = [
                # ==================== CONFIGURATION ====================
                Tool(
                    name="get_config",
                    description="Get current configuration",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="switch_codebase",
                    description="Switch to a different codebase. Use this to work with multiple repositories.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the codebase root (can be absolute or relative)"
                            }
                        },
                        "required": ["path"]
                    }
                ),
                Tool(
                    name="list_codebases",
                    description="Discover codebases by searching for .sln files and .git directories",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "search_path": {
                                "type": "string",
                                "description": "Directory to search (default: current working directory)"
                            }
                        },
                        "required": []
                    }
                ),

                # ==================== MEMORY TOOLS ====================
                Tool(
                    name="memory_save",
                    description="Save a finding to persistent memory",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "description": "Topic name"},
                            "content": {"type": "string", "description": "Finding content (markdown)"},
                            "category": {"type": "string", "description": "Category (default: findings)"}
                        },
                        "required": ["topic", "content"]
                    }
                ),
                Tool(
                    name="memory_get",
                    description="Retrieve a topic from memory",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "description": "Topic name"},
                            "category": {"type": "string", "description": "Category to search in (optional)"}
                        },
                        "required": ["topic"]
                    }
                ),
                Tool(
                    name="memory_search",
                    description="Search memory for keywords",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"}
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="memory_status",
                    description="Get memory store statistics",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),

                # ==================== SEMANTIC MEMORY TOOLS ====================
                Tool(
                    name="memory_semantic_search",
                    description="Search memory by meaning (semantic similarity), not just keywords. Finds related concepts even without exact word matches.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query in natural language"},
                            "limit": {"type": "number", "description": "Max results (default: 5)"},
                            "category": {"type": "string", "description": "Filter by category (optional)"}
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="memory_vector_stats",
                    description="Get vector memory statistics including embedding info and entry counts",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="context_offload",
                    description="Manually trigger context offload to memory. Useful when context is getting large.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "current_query": {
                                "type": "string",
                                "description": "Current work query for relevance scoring (helps keep relevant context)"
                            }
                        },
                        "required": []
                    }
                ),
                Tool(
                    name="context_retrieve",
                    description="Retrieve relevant memories from both working memory and offloaded context for a query",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "limit": {"type": "number", "description": "Max results (default: 3)"}
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="context_stats",
                    description="Get context tracking statistics including size, utilization, and item counts",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),

                # ==================== CONTEXT GATHERER TOOLS ====================
                Tool(
                    name="find_similar_code",
                    description="""Find similar code patterns in the codebase.

RETURNS: Code snippets with file paths showing how similar things are done
YOU (Claude) then: Compare patterns, choose best approach, explain reasoning

USE THIS TO:
- Understand established patterns before making changes
- Find examples of how similar problems were solved
- Ensure consistency with existing codebase
- Compare different implementation approaches

COMMON WORKFLOWS:
- Understanding a feature: lookup_convention → find_similar_code → extract_class_structure
- Planning changes: find_similar_code → get_callers → find_references

FOLLOW UP WITH:
- extract_class_structure() to dive deeper into specific files
- get_callers() to understand usage patterns
- find_implementations() to compare approaches

This tool DOES the finding, YOU do the thinking and deciding.""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Pattern to search for (e.g., 'DICOM handling', 'file processing')"
                            },
                            "file_pattern": {
                                "type": "string",
                                "description": "File pattern (default: *.cs)",
                                "default": "*.cs"
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum results to return (default: 5)",
                                "default": 5
                            }
                        },
                        "required": ["pattern"]
                    }
                ),
                Tool(
                    name="lookup_convention",
                    description="""Lookup how things are typically done in the codebase.

RETURNS: Examples showing established patterns and conventions
YOU (Claude) then: Decide whether to follow the convention or intentionally adapt

USE THIS FOR:
- Finding naming conventions
- Understanding error handling patterns
- Learning configuration approaches
- Seeing how logging is typically done
- Understanding async/await vs callback patterns

COMMON WORKFLOWS:
- Starting a task: lookup_convention → find_similar_code
- Before implementing: lookup_convention → find_implementations

This tool GATHERS examples, YOU decide whether to follow them.""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "topic": {
                                "type": "string",
                                "description": "Convention to lookup (e.g., 'error handling', 'logging', 'DI registration')"
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum results (default: 3)",
                                "default": 3
                            }
                        },
                        "required": ["topic"]
                    }
                ),
                Tool(
                    name="get_callers",
                    description="""Find all code that calls a specific method or class.

RETURNS: List of all callers with file paths and code context
YOU (Claude) then: Analyze impact, plan safe refactoring, understand what might break

USE THIS WHEN:
- Planning changes to a method
- Understanding what might break if you change something
- Finding ripple effects of changes
- Need to understand usage patterns

FOLLOW UP WITH:
- find_references() for detailed member usage
- trace_execution() to understand full flow""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "Method or class name (e.g., 'ProcessFile', 'BaseDistiller')"
                            }
                        },
                        "required": ["target"]
                    }
                ),
                Tool(
                    name="find_class_usages",
                    description="""Find all usages of a class or interface.

RETURNS: List of all places where class is used/instantiated/inherited
YOU (Claude) then: Analyze dependencies, understand coupling

USE THIS WHEN:
- Understanding how a class is used throughout codebase
- Planning changes to an interface
- Finding tight coupling issues

FOLLOW UP WITH:
- get_callers() for method-level usage
- find_implementations() to see implementations""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "class_name": {
                                "type": "string",
                                "description": "Class or interface name (e.g., 'BaseDistiller')"
                            }
                        },
                        "required": ["class_name"]
                    }
                ),
                Tool(
                    name="find_implementations",
                    description="""Find all implementations of an interface or abstract class.

RETURNS: List of all implementing classes with their key methods
YOU (Claude) then: Compare implementations, find patterns, choose reference

USE THIS WHEN:
- Understanding different implementations of a contract
- Comparing approaches across components
- Finding which implementation to use as reference

FOLLOW UP WITH:
- extract_class_structure() to compare specific implementations
- find_similar_code() to see related patterns""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "interface_name": {
                                "type": "string",
                                "description": "Interface or abstract class name"
                            }
                        },
                        "required": ["interface_name"]
                    }
                ),

                # ==================== CODE ANALYSIS TOOLS ====================
                Tool(
                    name="extract_class_structure",
                    description="""Parse a C# class and extract its structure.

RETURNS: Structured list of methods, properties, fields, events with signatures
YOU (Claude) then: Understand relationships, plan integration, design changes

SAVES YOUR CONTEXT: Instead of reading the whole file, get the structure first

USE THIS WHEN:
- Understanding a class before diving into details
- Planning how to extend or modify a class
- Comparing class structures

COMMON WORKFLOWS:
- Understanding a class: get_file_summary → extract_class_structure
- Refactoring: extract_class_structure → get_callers → find_references""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Path to the C# file (relative to codebase root)"
                            },
                            "include_body": {
                                "type": "boolean",
                                "description": "Include method bodies (default: false)",
                                "default": False
                            }
                        },
                        "required": ["file_path"]
                    }
                ),
                Tool(
                    name="get_file_summary",
                    description="""Get summary statistics for a file.

RETURNS: Line count, class count, namespace, imports, complexity metrics
YOU (Claude) then: Decide if deep analysis is needed

USE THIS WHEN:
- Quickly understanding file scope
- Deciding where to focus attention
- Assessing file complexity

FOLLOW UP WITH:
- extract_class_structure() for detailed analysis
- find_similar_code() to compare with other files""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Path to the file (relative to codebase root)"
                            }
                        },
                        "required": ["file_path"]
                    }
                ),
                Tool(
                    name="list_dependencies",
                    description="""Map all dependencies for a file or project.

RETURNS: List of all imports, references, project dependencies
YOU (Claude) then: Understand coupling, plan changes

USE THIS WHEN:
- Understanding what a file depends on
- Planning refactors that might affect dependencies
- Assessing coupling

FOLLOW UP WITH:
- get_callers() for reverse dependencies
- find_class_usages() for specific class usage""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "File path or project name"
                            }
                        },
                        "required": ["target"]
                    }
                ),
                Tool(
                    name="find_references",
                    description="""Find all references to a member (method, property, field).

RETURNS: List of all usages with context
YOU (Claude) then: Plan safe refactoring

USE THIS WHEN:
- Planning to rename or modify a member
- Understanding how something is used
- Finding all places that need updates

FOLLOW UP WITH:
- get_callers() for method/class level
- trace_execution() to understand flow""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "member_name": {
                                "type": "string",
                                "description": "Member name to find references for"
                            },
                            "file_pattern": {
                                "type": "string",
                                "description": "File pattern to search (default: *.cs)",
                                "default": "*.cs"
                            }
                        },
                        "required": ["member_name"]
                    }
                ),

                # ==================== GLM WORKER TOOLS ====================
                Tool(
                    name="summarize_large_file",
                    description="""GLM summarizes a large file to save your context.

GLM DOES: Read file, extract key points, summarize structure (limit: 10K chars)
YOU THEN: Use the summary to do your important thinking and reasoning

SAVES YOUR CONTEXT for: Complex reasoning about architecture, design decisions

USE THIS WHEN:
- File is too large to read in your context
- Want high-level understanding before diving deep
- Need to grasp structure quickly

NOTE: GLM only receives the file content (truncated), no extra context.""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Path to the file (relative to codebase root)"
                            },
                            "focus": {
                                "type": "string",
                                "description": "Specific focus (optional): 'classes', 'methods', 'dependencies'"
                            }
                        },
                        "required": ["file_path"]
                    }
                ),
                Tool(
                    name="get_alternative",
                    description="""Get an alternative approach from GLM for comparison.

GLM PROVIDES: Different way to solve the problem (with your code only, no extra context)
YOU (Claude) THEN: Evaluate pros/cons, decide whether to adopt

USE THIS WHEN:
- You want to consider options before deciding
- You're uncertain and want a second perspective
- Comparing multiple approaches

IMPORTANT: You (Claude) make the final decision. GLM just provides input.

CONTEXT LIMIT: Only your code is sent (max 10K chars), no memory dumping.""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "your_approach": {
                                "type": "string",
                                "description": "Describe your proposed solution or approach"
                            },
                            "context": {
                                "type": "string",
                                "description": "Additional context (optional, keep brief)"
                            }
                        },
                        "required": ["your_approach"]
                    }
                ),
                Tool(
                    name="risk_check",
                    description="""GLM identifies potential risks in your proposed approach.

GLM PROVIDES: List of potential issues, edge cases, problems (brief input only)
YOU (Claude) THEN: Validate which risks are real, prioritize them

USE THIS WHEN:
- Making architectural decisions
- Planning significant changes
- Reviewing complex code
- Before committing to an approach

IMPORTANT: You (Claude) validate the risks. GLM might hallucinate.

CONTEXT LIMIT: Your approach only (max 10K chars), no extra context.""",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "proposed_change": {
                                "type": "string",
                                "description": "Describe the change you're planning"
                            },
                            "code": {
                                "type": "string",
                                "description": "Relevant code snippet (optional, keep brief)"
                            }
                        },
                        "required": ["proposed_change"]
                    }
                ),

                # ==================== PROJECT-LEVEL TOOLS ====================
                Tool(
                    name="explore_project",
                    description="Explore a C# project and generate comprehensive summary",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "project": {"type": "string", "description": "Project name (e.g., 'MyProject')"}
                        },
                        "required": ["project"]
                    }
                ),
                Tool(
                    name="analyze_architecture",
                    description="Analyze overall solution architecture",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),

                # ==================== TASK MANAGEMENT ====================
                Tool(
                    name="task_start",
                    description="Start a new long-running task",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Task name"},
                            "description": {"type": "string", "description": "Task description"}
                        },
                        "required": ["name", "description"]
                    }
                ),
                Tool(
                    name="task_update",
                    description="Update a task with findings",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Task name"},
                            "content": {"type": "string", "description": "Update content (markdown)"}
                        },
                        "required": ["name", "content"]
                    }
                ),
                Tool(
                    name="task_status",
                    description="Get task status and history",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Task name"}
                        },
                        "required": ["name"]
                    }
                )
            ]

            return tools

        @self.app.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            """Handle tool calls"""
            try:
                # ==================== CONFIGURATION ====================
                if name == "get_config":
                    config_data = {
                        "glm_available": self.glm_available,
                        "language": "C#"
                    }
                    if self.codebase_path:
                        config_data["codebase_path"] = str(self.codebase_path)
                        config_data["memory_path"] = str(self.memory.memory_path) if self.memory else None
                        config_data["initialized"] = True
                    else:
                        config_data["initialized"] = False
                        config_data["message"] = "No codebase selected. Use switch_codebase() to select one."
                    return [TextContent(type="text", text=json.dumps(config_data, indent=2))]

                elif name == "switch_codebase":
                    result = self.switch_codebase(arguments["path"])
                    if result["success"]:
                        output = f"**Switched to codebase:**\n"
                        output += f"- Path: {result['codebase_path']}\n"
                        output += f"- C# files: {result['cs_files_count']}\n"
                        output += f"- Projects: {result['projects_count']}\n"
                        if result['solutions']:
                            output += f"- Solutions: {', '.join(result['solutions'])}\n"
                        output += f"- Memory: {result['memory_path']}\n"
                        return [TextContent(type="text", text=output)]
                    else:
                        return [TextContent(type="text", text=f"Error: {result['error']}")]

                elif name == "list_codebases":
                    search_path = arguments.get("search_path")
                    result = self.list_codebases(search_path)
                    if not result["success"]:
                        return [TextContent(type="text", text=f"Error: {result['error']}")]

                    output = f"**Found {result['codebases_count']} codebase(s)** in: {result['search_path']}\n\n"
                    for i, cb in enumerate(result["codebases"], 1):
                        output += f"{i}. **{cb['name']}** ({cb['type']})\n"
                        output += f"   Path: `{cb['root']}`\n"
                        output += f"   Switch with: `switch_codebase(path=\"{cb['root']}\")`\n\n"
                    return [TextContent(type="text", text=output)]

                # ==================== CHECK INITIALIZATION ====================
                # All tools below require a codebase to be initialized
                is_ready, error_msg = self._check_initialized()
                if not is_ready:
                    return [TextContent(type="text", text=error_msg)]

                # ==================== MEMORY TOOLS ====================
                elif name == "memory_save":
                    result = self.memory.save_finding(
                        topic=arguments["topic"],
                        content=arguments["content"],
                        category=arguments.get("category", "findings"),
                        metadata={"timestamp": datetime.now().isoformat()}
                    )
                    return [TextContent(type="text", text=f"Saved to memory: {result}")]

                elif name == "memory_get":
                    result = self.memory.get_topic(
                        topic=arguments["topic"],
                        category=arguments.get("category")
                    )
                    if result:
                        return [TextContent(type="text", text=result["content"])]
                    else:
                        return [TextContent(type="text", text=f"Topic '{arguments['topic']}' not found in memory")]

                elif name == "memory_search":
                    results = self.memory.search(arguments["query"])
                    if not results:
                        return [TextContent(type="text", text=f"No results found for '{arguments['query']}'")]

                    output = f"Found {len(results)} results:\n\n"
                    for r in results:
                        output += f"## {r['topic']} ({r['category']})\n"
                        output += f"{r['snippet']}\n\n"
                    return [TextContent(type="text", text=output)]

                elif name == "memory_status":
                    status = self.memory.get_status()
                    output = f"""Memory Store Status:
- Path: {status['memory_path']}
- Created: {status['created']}
- Last Updated: {status['last_updated']}
- Total Topics: {status['total_topics']}
- Topics by Category:"""
                    for cat, count in status['topics_by_category'].items():
                        output += f"\n  - {cat}: {count}"
                    return [TextContent(type="text", text=output)]

                # ==================== SEMANTIC MEMORY TOOLS ====================
                elif name == "memory_semantic_search":
                    if not self.vector_store:
                        return [TextContent(type="text", text="Semantic search not available. Install with: pip install -e '.[vector]'")]

                    query = arguments["query"]
                    limit = arguments.get("limit", 5)
                    category = arguments.get("category")

                    results = self.vector_store.search(query, limit=limit, category=category)

                    if not results:
                        return [TextContent(type="text", text=f"No semantic matches found for '{query}'")]

                    output = f"## Semantic Search Results: '{query}'\n\n"
                    for i, r in enumerate(results, 1):
                        output += f"### {i}. [{r['category']}] {r['topic']}\n"
                        output += f"**Relevance:** {r['score']:.2f}\n\n"
                        snippet = r['content'][:300] + "..." if len(r['content']) > 300 else r['content']
                        output += f"{snippet}\n\n"
                    return [TextContent(type="text", text=output)]

                elif name == "memory_vector_stats":
                    if not self.vector_store:
                        return [TextContent(type="text", text="Vector memory not available. Install with: pip install -e '.[vector]'")]

                    stats = self.vector_store.get_stats()
                    output = f"""Vector Memory Status:
- Database: {stats['db_path']}
- Embeddings Available: {stats['embeddings_available']}
- Model: {stats['embedding_model']}
- Total Entries: {stats['total_entries']}
- Entries by Category:"""
                    for cat, count in stats['categories'].items():
                        output += f"\n  - {cat}: {count}"
                    return [TextContent(type="text", text=output)]

                elif name == "context_offload":
                    if not self.context_tracker:
                        return [TextContent(type="text", text="Context tracking not available. Install with: pip install -e '.[vector]'")]

                    current_query = arguments.get("current_query", "")
                    result = self.context_tracker._trigger_offload(current_query)

                    output = f"## Context Offload Complete\n\n"
                    output += f"- Offloaded {result['offloaded_count']} items\n"
                    output += f"- Offloaded size: {result['offloaded_size']} chars\n"
                    output += f"- Remaining size: {result['remaining_size']} chars\n"
                    return [TextContent(type="text", text=output)]

                elif name == "context_retrieve":
                    if not self.context_tracker:
                        return [TextContent(type="text", text="Context tracking not available. Install with: pip install -e '.[vector]'")]

                    query = arguments["query"]
                    limit = arguments.get("limit", 3)

                    results = self.context_tracker.retrieve_relevant(query, limit=limit)

                    output = f"## Relevant Context: '{query}'\n\n"
                    for i, r in enumerate(results, 1):
                        output += f"### {i}. [{r.get('source', 'unknown')}] {r.get('item_type', 'general')}\n"
                        if 'score' in r:
                            output += f"**Relevance:** {r['score']:.2f}\n\n"
                        output += f"{r.get('content', '')[:300]}\n\n"
                    return [TextContent(type="text", text=output)]

                elif name == "context_stats":
                    if not self.context_tracker:
                        return [TextContent(type="text", text="Context tracking not available. Install with: pip install -e '.[vector]'")]

                    stats = self.context_tracker.get_stats()
                    output = f"""Context Tracking Status:
- Current Size: {stats['current_size']} chars
- Item Count: {stats['item_count']}
- Threshold: {stats['threshold']} chars
- Utilization: {stats['utilization']:.1%}
- Offloaded Items: {stats['offloaded_count']}
- Items by Type:"""
                    for item_type, count in stats['items_by_type'].items():
                        output += f"\n  - {item_type}: {count}"
                    return [TextContent(type="text", text=output)]

                # ==================== CONTEXT GATHERER TOOLS ====================
                elif name == "find_similar_code":
                    pattern = arguments["pattern"]
                    file_pattern = arguments.get("file_pattern", "*.cs")
                    max_results = arguments.get("max_results", 5)

                    matches = self.analyzer.find_pattern(pattern, file_pattern)

                    output = f"## Found {len(matches)} matches for '{pattern}':\n\n"

                    for i, match in enumerate(matches[:max_results], 1):
                        output += f"### {i}. {match['file']}\n\n"
                        for j, match_line in enumerate(match['matches'][:3], 1):
                            output += f"  Line {match_line['line_number']}: {match_line['content']}\n"
                        if len(match['matches']) > 3:
                            output += f"  ... and {len(match['matches']) - 3} more matches\n"
                        output += "\n"

                    output += "\n**YOU (Claude)** should analyze these examples and decide how to proceed."
                    return [TextContent(type="text", text=output)]

                elif name == "lookup_convention":
                    topic = arguments["topic"]
                    max_results = arguments.get("max_results", 3)

                    memory_results = self.memory.search(topic)
                    code_matches = self.analyzer.find_pattern(topic, "*.cs")

                    output = f"## Convention lookup: '{topic}'\n\n"

                    if memory_results:
                        output += f"### From memory:\n"
                        for r in memory_results[:max_results]:
                            output += f"- {r['topic']}: {r['snippet'][:100]}...\n"
                        output += "\n"

                    if code_matches:
                        output += f"### Code examples:\n\n"
                        for i, match in enumerate(code_matches[:max_results], 1):
                            output += f"{i}. **{match['file']}**\n"
                            for match_line in match['matches'][:2]:
                                output += f"   {match_line['content']}\n"
                            output += "\n"

                    output += "\n**YOU (Claude)** should analyze these conventions and decide whether to follow them."
                    return [TextContent(type="text", text=output)]

                elif name == "get_callers":
                    target = arguments["target"]

                    callers = []
                    for cs_file in self.codebase_path.rglob("*.cs"):
                        try:
                            with open(cs_file, 'r', encoding='utf-8') as f:
                                content = f.read()
                                lines = content.split('\n')

                            for i, line in enumerate(lines, 1):
                                if re.search(rf'\b{re.escape(target)}\s*\(', line):
                                    start = max(0, i - 3)
                                    context_lines = lines[start:min(len(lines), i + 2)]
                                    callers.append({
                                        "file": str(cs_file.relative_to(self.codebase_path)),
                                        "line": i,
                                        "context": '\n'.join(context_lines)
                                    })

                                    if len(callers) >= 50:
                                        break
                        except Exception:
                            continue

                    output = f"## Callers of '{target}':\n\n"
                    if not callers:
                        output += f"No callers found for '{target}'\n"
                    else:
                        output += f"Found {len(callers)} callers:\n\n"
                        for caller in callers[:20]:
                            output += f"### {caller['file']}:{caller['line']}\n"
                            output += f"```\n{caller['context']}\n```\n\n"
                        if len(callers) > 20:
                            output += f"... and {len(callers) - 20} more\n"

                    output += "\n**YOU (Claude)** should analyze these callers to understand impact."
                    return [TextContent(type="text", text=output)]

                elif name == "find_class_usages":
                    class_name = arguments["class_name"]

                    result = self.analyzer.find_class_usages(class_name)

                    output = f"## Usages of '{class_name}':\n\n"
                    output += f"Total usages: {result['total_usages']}\n"
                    output += f"Files affected: {result['files_affected']}\n\n"

                    for file, usages in list(result['by_file'].items())[:10]:
                        output += f"### {file}\n"
                        output += f"Usage count: {len(usages)}\n"
                        for usage in usages[:3]:
                            output += f"  Line {usage['line']} ({usage['type']}): {usage['context'][:60]}...\n"
                        output += "\n"

                    if len(result['by_file']) > 10:
                        output += f"... and {len(result['by_file']) - 10} more files\n"

                    output += "\n**YOU (Claude)** should analyze these usages to understand dependencies."
                    return [TextContent(type="text", text=output)]

                elif name == "find_implementations":
                    interface_name = arguments["interface_name"]

                    implementations = self.analyzer.find_implementations(interface_name)

                    output = f"## Implementations of '{interface_name}':\n\n"
                    if not implementations:
                        output += f"No implementations found for '{interface_name}'\n"
                    else:
                        for impl in implementations:
                            output += f"### {impl['class']} ({impl['file']})\n"
                            output += f"Key methods: {', '.join(impl['methods'][:5])}"
                            if len(impl['methods']) > 5:
                                output += f" ... and {len(impl['methods']) - 5} more"
                            output += "\n\n"

                    output += "\n**YOU (Claude)** should compare these implementations and recommend patterns."
                    return [TextContent(type="text", text=output)]

                # ==================== CODE ANALYSIS TOOLS ====================
                elif name == "extract_class_structure":
                    file_path = self.codebase_path / arguments["file_path"]
                    include_body = arguments.get("include_body", False)

                    if not file_path.exists():
                        return [TextContent(type="text", text=f"File not found: {arguments['file_path']}")]

                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # Extract namespace
                    ns_match = re.search(r'namespace\s+([\w.]+)', content)
                    namespace = ns_match.group(1) if ns_match else None

                    # Extract class/interface names
                    class_matches = re.finditer(
                        r'(public|internal|private|protected)?\s*(abstract|sealed|static)?\s*(class|interface|struct|record)\s+(\w+)',
                        content
                    )

                    output = f"## Structure of {arguments['file_path']}\n\n"
                    if namespace:
                        output += f"**Namespace:** {namespace}\n\n"

                    structures = []
                    for match in class_matches:
                        struct_type = match.group(2) or ""
                        kind = match.group(3)
                        name = match.group(4)

                        # Find members for this class
                        class_start = match.end()
                        brace_count = 0
                        in_class = False
                        class_content = ""

                        for char in content[class_start:]:
                            if char == '{':
                                brace_count += 1
                                in_class = True
                            elif char == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    break
                            if in_class:
                                class_content += char

                        # Extract members
                        if include_body:
                            member_matches = re.finditer(
                                rf'(public|internal|protected|private).*?(\w+)\s*\([^)]*\)\s*{{',
                                class_content,
                                re.MULTILINE | re.DOTALL
                            )
                        else:
                            member_matches = re.finditer(
                                rf'(public|internal|protected|private).*?(\w+(?:<[^>]+>)?)\s*\([^)]*\)\s*{{?',
                                class_content,
                                re.MULTILINE
                            )

                        members = []
                        for m in member_matches:
                            members.append(f"  {m.group(0).strip().replace('{', '').strip()}")

                        structures.append({
                            "type": struct_type,
                            "kind": kind,
                            "name": name,
                            "members": members
                        })

                    # Format output
                    for struct in structures:
                        modifiers = " ".join(filter(None, [struct["type"], struct["kind"]]))
                        output += f"### {modifiers} {struct['name']}\n\n"
                        if struct["members"]:
                            output += f"**Members:**\n"
                            for member in struct["members"][:20]:
                                output += f"{member}\n"
                            if len(struct["members"]) > 20:
                                output += f"  ... and {len(struct['members']) - 20} more\n"
                        else:
                            output += "No members found\n"
                        output += "\n"

                    output += "\n**YOU (Claude)** should analyze this structure to understand relationships."
                    return [TextContent(type="text", text=output)]

                elif name == "get_file_summary":
                    file_path = self.codebase_path / arguments["file_path"]

                    if not file_path.exists():
                        return [TextContent(type="text", text=f"File not found: {arguments['file_path']}")]

                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        lines = content.split('\n')

                    # Extract stats
                    total_lines = len(lines)
                    code_lines = len([l for l in lines if l.strip() and not l.strip().startswith('//')])
                    empty_lines = len([l for l in lines if not l.strip()])

                    # Count using statements
                    usings = len(re.findall(r'using\s+', content))

                    # Count classes
                    classes = len(re.findall(r'\bclass\s+\w+', content))

                    # Count methods
                    methods = len(re.findall(r'\s+\w+\s*\([^)]*\)\s*{', content))

                    # Find namespace
                    ns_match = re.search(r'namespace\s+([\w.]+)', content)
                    namespace = ns_match.group(1) if ns_match else "None"

                    output = f"## File Summary: {arguments['file_path']}\n\n"
                    output += f"**Total Lines:** {total_lines}\n"
                    output += f"**Code Lines:** {code_lines}\n"
                    output += f"**Empty Lines:** {empty_lines}\n"
                    output += f"**Using Statements:** {usings}\n"
                    output += f"**Classes:** {classes}\n"
                    output += f"**Methods:** {methods}\n"
                    output += f"**Namespace:** {namespace}\n\n"

                    # Complexity estimate
                    if total_lines > 500:
                        complexity = "High"
                    elif total_lines > 200:
                        complexity = "Medium"
                    else:
                        complexity = "Low"

                    output += f"**Complexity:** {complexity}\n\n"

                    output += "\n**YOU (Claude)** should decide if this file needs deeper analysis."
                    return [TextContent(type="text", text=output)]

                elif name == "list_dependencies":
                    target = arguments["target"]

                    # Check if it's a file or project
                    target_path = self.codebase_path / target

                    output = f"## Dependencies for '{target}'\n\n"

                    if target_path.exists() and target_path.is_file():
                        # File dependencies
                        with open(target_path, 'r', encoding='utf-8') as f:
                            content = f.read()

                        # Extract using statements
                        usings = re.findall(r'using\s+([\w.]+);', content)

                        # Group by namespace
                        system_usings = []
                        project_usings = []
                        for using in usings:
                            if using.startswith('System'):
                                system_usings.append(using)
                            else:
                                project_usings.append(using)

                        output += f"**File:** {target}\n\n"
                        output += f"### System Dependencies ({len(system_usings)})\n"
                        for u in sorted(set(system_usings))[:20]:
                            output += f"- {u}\n"

                        output += f"\n### Project Dependencies ({len(project_usings)})\n"
                        for u in sorted(set(project_usings))[:20]:
                            output += f"- {u}\n"

                    else:
                        # Project dependencies
                        proj_file = self.codebase_path / f"{target}.csproj"
                        if proj_file.exists():
                            with open(proj_file, 'r', encoding='utf-8') as f:
                                proj_content = f.read()

                            # Package references
                            packages = re.findall(r'<PackageReference\s+Include="([^"]+)"', proj_content)

                            output += f"**Project:** {target}\n\n"
                            output += f"### NuGet Packages ({len(packages)})\n"
                            for pkg in packages:
                                output += f"- {pkg}\n"
                        else:
                            output += f"Project '{target}' not found.\n"

                    output += "\n**YOU (Claude)** should analyze dependencies to understand coupling."
                    return [TextContent(type="text", text=output)]

                elif name == "find_references":
                    member_name = arguments["member_name"]
                    file_pattern = arguments.get("file_pattern", "*.cs")

                    references = []
                    for cs_file in self.codebase_path.rglob(file_pattern):
                        try:
                            with open(cs_file, 'r', encoding='utf-8') as f:
                                content = f.read()
                                lines = content.split('\n')

                            for i, line in enumerate(lines, 1):
                                if re.search(rf'\b{re.escape(member_name)}\b', line):
                                    references.append({
                                        "file": str(cs_file.relative_to(self.codebase_path)),
                                        "line": i,
                                        "code": line.strip()
                                    })

                                    if len(references) >= 50:
                                        break
                        except Exception:
                            continue

                    output = f"## References to '{member_name}':\n\n"
                    output += f"Found {len(references)} references:\n\n"

                    # Group by file
                    by_file = {}
                    for ref in references:
                        file = ref["file"]
                        if file not in by_file:
                            by_file[file] = []
                        by_file[file].append(ref)

                    for file, refs in list(by_file.items())[:10]:
                        output += f"### {file}\n"
                        output += f"Count: {len(refs)}\n"
                        for ref in refs[:5]:
                            output += f"  Line {ref['line']}: {ref['code'][:80]}\n"
                        output += "\n"

                    if len(by_file) > 10:
                        output += f"... and {len(by_file) - 10} more files\n"

                    output += "\n**YOU (Claude)** should use this to plan safe refactoring."
                    return [TextContent(type="text", text=output)]

                # ==================== GLM WORKER TOOLS ====================
                elif name == "summarize_large_file":
                    if not self.glm_available:
                        return [TextContent(type="text", text="GLM API key not configured. Add GLM_API_KEY to environment variables or .env file.")]

                    file_path = self.codebase_path / arguments["file_path"]
                    focus = arguments.get("focus", "")

                    if not file_path.exists():
                        return [TextContent(type="text", text=f"File not found: {arguments['file_path']}")]

                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # Truncate to context limit
                    truncated_content = self._truncate_for_glm(content)

                    prompt = f"""Summarize this file:

{f"File: {arguments['file_path']}"}
{f"Focus: {focus}" if focus else ""}

File content (truncated to {len(truncated_content)} chars):
```
{truncated_content}
```

Provide:
1. Overall purpose
2. Main classes and their roles
3. Key methods and what they do
4. Important patterns or conventions"""

                    result = self.glm.explore(
                        question=f"Summarize file: {arguments['file_path']}",
                        context=prompt,
                        max_tokens=2048
                    )

                    return [TextContent(type="text", text=f"**GLM Summary:**\n\n{result}")]

                elif name == "get_alternative":
                    if not self.glm_available:
                        return [TextContent(type="text", text="GLM API key not configured.")]

                    your_approach = arguments["your_approach"]
                    context = arguments.get("context", "")

                    prompt = f"""I have this approach for solving a problem:

{your_approach}

{f"Additional context: {context}" if context else ""}

Can you suggest an alternative approach? Keep it practical and concise."""

                    result = self.glm.explore(
                        question="Alternative approach",
                        context=prompt,
                        max_tokens=2048
                    )

                    output = f"**GLM's Alternative:**\n\n{result}\n\n**YOU (Claude)** should evaluate this and decide whether to adopt it."

                    # Auto-capture to vector memory
                    self._maybe_auto_capture(name, arguments, output)

                    return [TextContent(type="text", text=output)]

                elif name == "risk_check":
                    if not self.glm_available:
                        return [TextContent(type="text", text="GLM API key not configured.")]

                    proposed_change = arguments["proposed_change"]
                    code = arguments.get("code", "")

                    prompt = f"""I'm planning this change:

{proposed_change}

{f"Relevant code:\n```\n{self._truncate_for_glm(code, 3000)}\n```" if code else ""}

What are the potential risks, edge cases, or problems? Be concise and practical."""

                    result = self.glm.explore(
                        question="Risk check",
                        context=prompt,
                        max_tokens=2048
                    )

                    output = f"**GLM's Risk Assessment:**\n\n{result}\n\n**YOU (Claude)** should validate which risks are real and prioritize them."

                    # Auto-capture to vector memory
                    self._maybe_auto_capture(name, arguments, output)

                    return [TextContent(type="text", text=output)]

                # ==================== PROJECT-LEVEL TOOLS ====================
                elif name == "explore_project":
                    result = self.analyzer.analyze_project(arguments["project"])

                    if "error" in result:
                        return [TextContent(type="text", text=result["error"])]

                    summary = f"""# {result['project_name']} Project Summary

**Path:** `{result['path']}`
**Total Files:** {result['total_files']}

## Namespaces
{chr(10).join(f"- {ns}" for ns in result['namespaces'])}

## Classes ({len(result['classes'])})
{chr(10).join(f"- **{c['name']}** ({c.get('namespace', 'N/A')})" for c in result['classes'][:20])}
{f"\n_... and {len(result['classes']) - 20} more_" if len(result['classes']) > 20 else ""}

## Project References
{chr(10).join(f"- {ref}" for ref in result['project_references']) if result['project_references'] else "None"}

## Package References
{chr(10).join(f"- **{pkg['name']}** ({pkg['version']})" for pkg in result['package_references'])}
"""

                    self.memory.save_finding(
                        topic=result['project_name'],
                        content=summary,
                        category="architecture"
                    )

                    # Auto-capture to vector memory
                    self._maybe_auto_capture(name, arguments, summary)

                    return [TextContent(type="text", text=summary)]

                elif name == "analyze_architecture":
                    result = self.analyzer.analyze_architecture()

                    output = f"""# Solution Architecture Overview

**Total Projects:** {result['total_projects']}

## Applications
{chr(10).join(f"- {p}" for p in result['categories']['apps'])}

## Libraries
{chr(10).join(f"- {p}" for p in result['categories']['libraries'])}

## Tests
{chr(10).join(f"- {p}" for p in result['categories']['tests'])}
"""

                    self.memory.save_finding(
                        topic="architecture-overview",
                        content=output,
                        category="architecture"
                    )

                    # Auto-capture to vector memory
                    self._maybe_auto_capture(name, arguments, output)

                    return [TextContent(type="text", text=output)]

                # ==================== TASK TOOLS ====================
                elif name == "task_start":
                    task_content = f"""# Task: {arguments['name']}

**Started:** {datetime.now().isoformat()}

## Description
{arguments['description']}

## Progress

"""
                    self.memory.save_finding(
                        topic=arguments['name'],
                        content=task_content,
                        category=f"tasks/active"
                    )

                    return [TextContent(type="text", text=f"Task '{arguments['name']}' started. Use task_update to add progress.")]

                elif name == "task_update":
                    existing = self.memory.get_topic(arguments['name'], category="tasks/active")

                    if not existing:
                        return [TextContent(type="text", text=f"Task '{arguments['name']}' not found. Use task_start first.")]

                    updated_content = existing['content'] + f"\n### Update {datetime.now().isoformat()}\n\n{arguments['content']}\n"

                    self.memory.save_finding(
                        topic=arguments['name'],
                        content=updated_content,
                        category="tasks/active"
                    )

                    return [TextContent(type="text", text=f"Task '{arguments['name']}' updated.")]

                elif name == "task_status":
                    result = self.memory.get_topic(arguments['name'], category="tasks/active")

                    if result:
                        return [TextContent(type="text", text=result['content'])]
                    else:
                        return [TextContent(type="text", text=f"Task '{arguments['name']}' not found.")]

                else:
                    return [TextContent(type="text", text=f"Unknown tool: {name}")]

            except Exception as e:
                import traceback
                return [TextContent(type="text", text=f"Error: {str(e)}\n\nTraceback:\n{traceback.format_exc()}")]

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
    # The server uses the current working directory set by Claude Desktop's "cwd" config
    # No need to pass codebase_path - it will be auto-detected from cwd
    server = ClaudeCollaboratorServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
