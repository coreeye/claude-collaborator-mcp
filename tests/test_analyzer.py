"""
Basic tests for C# code analyzer
"""

import unittest
from pathlib import Path
import sys

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claude_collaborator.code_analyzer import CSharpCodeAnalyzer


class TestCSharpCodeAnalyzer(unittest.TestCase):
    """Test cases for CSharpCodeAnalyzer"""

    def setUp(self):
        """Set up test fixtures"""
        self.test_dir = Path(__file__).parent.parent / "examples" / "simple-csharp"

    def test_analyzer_initialization(self):
        """Test analyzer can be initialized"""
        if not self.test_dir.exists():
            self.skipTest("Test project not yet created")
        analyzer = CSharpCodeAnalyzer(str(self.test_dir))
        self.assertIsNotNone(analyzer.codebase_path)

    def test_analyze_architecture(self):
        """Test architecture analysis"""
        if not self.test_dir.exists():
            self.skipTest("Test project not yet created")
        analyzer = CSharpCodeAnalyzer(str(self.test_dir))
        result = analyzer.analyze_architecture()
        self.assertIn("total_projects", result)

    def test_find_pattern(self):
        """Test pattern finding"""
        if not self.test_dir.exists():
            self.skipTest("Test project not yet created")
        analyzer = CSharpCodeAnalyzer(str(self.test_dir))
        results = analyzer.find_pattern("class")
        self.assertIsInstance(results, list)


if __name__ == "__main__":
    unittest.main()
