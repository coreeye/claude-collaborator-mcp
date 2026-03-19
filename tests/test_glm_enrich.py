"""
Tests for GLM auto-enrich and proactive suggestions
"""

import unittest
import time
import threading
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from mcp.types import TextContent

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claude_collaborator.server import ClaudeCollaboratorServer
from claude_collaborator.code_analyzer import CSharpCodeAnalyzer


class TestGLMAutoEnrich(unittest.TestCase):
    """Test cases for GLM auto-enrich functionality"""

    def setUp(self):
        """Set up test fixtures"""
        self.test_dir = Path(__file__).parent.parent / "examples" / "simple-csharp"

        if not self.test_dir.exists():
            self.skipTest("Test project not found")

    @patch('claude_collaborator.server.GLMClient')
    def test_auto_enrich_thread_started(self, mock_glm_class):
        """Test that auto-enrich starts a background thread"""
        # Mock GLM client
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm

        # Create server
        server = ClaudeCollaboratorServer(str(self.test_dir))

        # Track whether thread was started
        thread_started = threading.Event()
        original_thread_start = threading.Thread.start

        def patched_start(self):
            thread_started.set()
            return original_thread_start(self)

        with patch.object(threading.Thread, 'start', patched_start):
            # Trigger auto-enrich by calling _process_tool_result
            result = server._auto_enrich_with_glm(
                "extract_class_structure",
                {"file_path": "test.cs"},
                "class Foo {}"
            )

        # Verify thread was started
        self.assertTrue(thread_started.wait(timeout=1), "Background thread should be started")

    @patch('claude_collaborator.server.GLMClient')
    def test_auto_enrich_disabled_when_config_false(self, mock_glm_class):
        """Test that auto-enrich is disabled when config is false"""
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm

        server = ClaudeCollaboratorServer(str(self.test_dir))
        server.config._config["auto_glm_enrich"] = False

        # Track GLM calls
        glm_called = threading.Event()

        def mock_explore(*args, **kwargs):
            glm_called.set()
            return "response"

        mock_glm.explore = mock_explore

        # Trigger auto-enrich
        server._auto_enrich_with_glm(
            "extract_class_structure",
            {"file_path": "test.cs"},
            "class Foo {}"
        )

        # Wait a bit to ensure GLM wasn't called
        time.sleep(0.5)
        self.assertFalse(glm_called.is_set(), "GLM should not be called when disabled")

    @patch('claude_collaborator.server.GLMClient')
    def test_auto_enrich_not_triggered_for_unlisted_tools(self, mock_glm_class):
        """Test that auto-enrich only runs for specified tools"""
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm

        server = ClaudeCollaboratorServer(str(self.test_dir))

        # Track GLM calls
        glm_called = threading.Event()

        def mock_explore(*args, **kwargs):
            glm_called.set()
            return "response"

        mock_glm.explore = mock_explore

        # Use a tool that's NOT in the auto-enrich list
        server._auto_enrich_with_glm(
            "get_config",  # Not in _auto_enrich_tools
            {},
            "{}"
        )

        # Wait a bit to ensure GLM wasn't called
        time.sleep(0.5)
        self.assertFalse(glm_called.is_set(), "GLM should not be called for unlisted tools")

    @patch('claude_collaborator.server.GLMClient')
    def test_proactive_suggestion_large_result(self, mock_glm_class):
        """Test proactive suggestion for large results"""
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm

        server = ClaudeCollaboratorServer(str(self.test_dir))

        # Create a result larger than 5000 chars
        large_result = "x" * 6000

        suggestion = server._get_glm_suggestion("some_tool", {}, large_result)

        self.assertIsNotNone(suggestion)
        self.assertIn("summarize_large_file", suggestion)
        self.assertIn("GLM Tip", suggestion)

    @patch('claude_collaborator.server.GLMClient')
    def test_proactive_suggestion_discovery_tools(self, mock_glm_class):
        """Test proactive suggestion for discovery tools"""
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm

        server = ClaudeCollaboratorServer(str(self.test_dir))

        discovery_tools = ["find_class_usages", "find_implementations", "find_references",
                          "list_dependencies", "find_similar_code", "lookup_convention"]

        for tool in discovery_tools:
            suggestion = server._get_glm_suggestion(tool, {}, "small result")
            self.assertIsNotNone(suggestion, f"Should suggest for {tool}")
            self.assertIn("glm_explore", suggestion)

    @patch('claude_collaborator.server.GLMClient')
    def test_proactive_suggestion_analysis_tools(self, mock_glm_class):
        """Test proactive suggestion for analysis tools"""
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm

        server = ClaudeCollaboratorServer(str(self.test_dir))

        analysis_tools = ["extract_class_structure", "get_file_summary",
                         "explore_project", "analyze_architecture"]

        for tool in analysis_tools:
            suggestion = server._get_glm_suggestion(tool, {}, "small result")
            self.assertIsNotNone(suggestion, f"Should suggest for {tool}")
            self.assertIn("get_alternative", suggestion)

    @patch('claude_collaborator.server.GLMClient')
    def test_proactive_suggestion_pattern_matching(self, mock_glm_class):
        """Test proactive suggestion for pattern matching"""
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm

        server = ClaudeCollaboratorServer(str(self.test_dir))

        # Test with pattern in arguments - should suggest risk_check
        # Use a tool not in discovery or analysis lists
        suggestion = server._get_glm_suggestion(
            "some_random_tool",
            {"pattern": "test"},
            "result"
        )
        self.assertIsNotNone(suggestion)
        self.assertIn("risk_check", suggestion)

        # Test that lookup_convention without pattern argument gives discovery tip
        suggestion = server._get_glm_suggestion(
            "lookup_convention",
            {},  # No pattern argument
            "result"
        )
        self.assertIsNotNone(suggestion)
        self.assertIn("glm_explore", suggestion)

    @patch('claude_collaborator.server.GLMClient')
    def test_proactive_suggestion_none_when_glm_unavailable(self, mock_glm_class):
        """Test that no suggestion is given when GLM is unavailable"""
        mock_glm_class.side_effect = ValueError("No API key")

        server = ClaudeCollaboratorServer(str(self.test_dir))

        suggestion = server._get_glm_suggestion(
            "extract_class_structure",
            {},
            "x" * 6000
        )

        self.assertIsNone(suggestion)

    @patch('claude_collaborator.server.GLMClient')
    def test_proactive_suggestion_disabled_when_config_false(self, mock_glm_class):
        """Test that proactive suggestions are disabled when config is false"""
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm

        server = ClaudeCollaboratorServer(str(self.test_dir))
        server.config._config["glm_proactive_suggestions"] = False

        suggestion = server._get_glm_suggestion(
            "extract_class_structure",
            {},
            "x" * 6000
        )

        self.assertIsNone(suggestion)

    @patch('claude_collaborator.server.GLMClient')
    def test_process_tool_result_includes_suggestion(self, mock_glm_class):
        """Test that _process_tool_result includes proactive suggestions"""
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm

        server = ClaudeCollaboratorServer(str(self.test_dir))

        # Create a result from an analysis tool (should trigger suggestion)
        result_text = "Some analysis result"

        processed = server._process_tool_result(
            "extract_class_structure",
            {"file_path": "test.cs"},
            [TextContent(type="text", text=result_text)]
        )

        # Check that suggestion is appended
        final_text = processed[0].text
        self.assertIn(result_text, final_text)
        self.assertIn("GLM Tip", final_text)
        self.assertIn("get_alternative", final_text)

    @patch('claude_collaborator.server.GLMClient')
    def test_glm_enrich_result_stored(self, mock_glm_class):
        """Test that GLM enrich results are stored properly"""
        # Create a mock GLM that responds with specific text
        mock_glm = Mock()
        mock_glm_class.return_value = mock_glm
        mock_glm.explore.return_value = "GLM analysis result"

        server = ClaudeCollaboratorServer(str(self.test_dir))

        # Track completion
        enrich_complete = threading.Event()

        # Override the GLM explore to track completion
        original_enrich = server._auto_enrich_with_glm

        def tracking_enrich(tool_name, arguments, result_text):
            original_enrich(tool_name, arguments, result_text)
            # Wait a bit for thread to complete
            time.sleep(0.2)
            enrich_complete.set()

        server._auto_enrich_with_glm = tracking_enrich

        # Trigger enrichment
        server._auto_enrich_with_glm(
            "extract_class_structure",
            {"file_path": "test.cs"},
            "class Foo {}"
        )

        # Wait for completion
        self.assertTrue(enrich_complete.wait(timeout=2), "Enrichment should complete")

        # Check that result was stored
        # Note: The key format is: {tool_name}_{hash(str(arguments))}_{timestamp}
        with server._glm_enrich_lock:
            # There should be at least one result
            self.assertGreater(len(server._glm_enrich_results), 0)

            # Check the structure of stored result
            for key, value in server._glm_enrich_results.items():
                self.assertIn("tool", value)
                self.assertIn("timestamp", value)
                self.assertIn("response", value)
                self.assertEqual(value["tool"], "extract_class_structure")
                break


class TestGLMConfiguration(unittest.TestCase):
    """Test cases for GLM configuration options"""

    def test_default_config_enables_auto_enrich(self):
        """Test that auto_glm_enrich defaults to true"""
        from claude_collaborator.config import Config

        config = Config()
        self.assertTrue(config.get("auto_glm_enrich", True))

    def test_default_config_enables_suggestions(self):
        """Test that glm_proactive_suggestions defaults to true"""
        from claude_collaborator.config import Config

        config = Config()
        self.assertTrue(config.get("glm_proactive_suggestions", True))


if __name__ == "__main__":
    unittest.main()
