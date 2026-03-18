"""
Code Analyzer for C# Codebases
Generic C# code parsing and analysis
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class CSharpCodeAnalyzer:
    """Analyzes C# codebases"""

    def __init__(self, codebase_path: str):
        """
        Initialize the analyzer

        Args:
            codebase_path: Path to the C# codebase (root with .sln file)
        """
        self.codebase_path = Path(codebase_path)

    def analyze_project(self, project_name: str) -> Dict[str, Any]:
        """
        Analyze a C# project

        Args:
            project_name: Name of the .csproj file (without extension)

        Returns:
            Project analysis with structure, classes, dependencies
        """
        # Find the project file
        project_file = self.codebase_path / f"{project_name}.csproj"
        if not project_file.exists():
            # Also try with pattern
            for proj in self.codebase_path.rglob("*.csproj"):
                if proj.stem == project_name:
                    project_file = proj
                    break

        if not project_file.exists():
            return {"error": f"Project file not found for: {project_name}"}

        with open(project_file, 'r', encoding='utf-8') as f:
            proj_content = f.read()

        # Get the directory containing the project
        project_dir = project_file.parent

        # Find all C# files in the project
        cs_files = list(project_dir.rglob("*.cs"))

        # Analyze files
        classes = []
        namespaces = set()
        usings = []

        for file_path in cs_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Extract namespace
                ns_match = re.search(r'namespace\s+([\w.]+)', content)
                if ns_match:
                    namespaces.add(ns_match.group(1))

                # Extract classes
                class_matches = re.finditer(
                    r'(?:public|internal|private|protected)?\s*(?:abstract|sealed|static)?\s*class\s+(\w+)',
                    content
                )
                for match in class_matches:
                    class_name = match.group(1)

                    # Get base class if any
                    base_match = re.search(
                        rf'class\s+{class_name}\s*:\s*(\w+)',
                        content
                    )
                    base_class = base_match.group(1) if base_match else None

                    classes.append({
                        "name": class_name,
                        "file": str(file_path.relative_to(self.codebase_path)),
                        "namespace": ns_match.group(1) if ns_match else None,
                        "base_class": base_class
                    })

                # Extract using statements
                using_matches = re.finditer(r'using\s+([\w.]+);', content)
                for match in using_matches:
                    usings.append(match.group(1))

            except Exception:
                pass

        # Find project references
        ref_matches = re.finditer(
            r'<ProjectReference\s+Include="([^"]+)"',
            proj_content
        )
        project_references = [match.group(1) for match in ref_matches]

        # Find package references
        pkg_matches = re.finditer(
            r'<PackageReference\s+Include="([^"]+)"',
            proj_content
        )
        package_references = []
        for match in pkg_matches:
            version_match = re.search(
                r'<PackageReference[^>]*Include="[^"]*"\s+Version="([^"]+)"',
                match.group(0)
            )
            version = version_match.group(1) if version_match else "unknown"
            package_references.append({
                "name": match.group(1),
                "version": version
            })

        return {
            "project_name": project_name,
            "path": str(project_file.relative_to(self.codebase_path)),
            "total_files": len(cs_files),
            "namespaces": sorted(list(namespaces)),
            "classes": classes,
            "project_references": project_references,
            "package_references": package_references,
            "external_usings": sorted(set(usings))
        }

    def analyze_architecture(self) -> Dict[str, Any]:
        """
        Analyze overall solution architecture

        Returns:
            Architecture overview with projects categorized
        """
        # Find all projects
        projects = {}
        categories = {
            "apps": [],
            "libraries": [],
            "tests": [],
            "other": []
        }

        for sln_file in self.codebase_path.rglob("*.sln"):
            # Get solution name
            sln_name = sln_file.stem

            # Read .sln file to extract projects (simplified)
            with open(sln_file, 'r', encoding='utf-8') as f:
                content = f.read()
                # Find project references
                proj_matches = re.findall(
                    r'"([^"]+\.csproj"',
                    content
                )
                for proj in proj_matches:
                    proj_name = proj.replace('\\', '/').split('/')[-1].replace('.csproj', '')
                    projects[proj_name] = proj

        # Categorize projects
        for project_name in projects.keys():
            if "Test" in project_name:
                categories["tests"].append(project_name)
            elif project_name.startswith("Lib") or project_name.startswith("Core"):
                categories["libraries"].append(project_name)
            elif "App" in project_name or "Service" in project_name or "Client" in project_name:
                categories["apps"].append(project_name)
            else:
                categories["other"].append(project_name)

        return {
            "total_projects": len(projects),
            "projects": projects,
            "categories": categories
        }

    def find_pattern(self, pattern_desc: str, file_pattern: str = "*.cs") -> List[Dict[str, Any]]:
        """
        Find files matching a pattern description

        Args:
            pattern_desc: Description of pattern to search for
            file_pattern: Glob pattern for files (default: *.cs)

        Returns:
            List of matching files with context
        """
        pattern_keywords = pattern_desc.lower().split()
        matches = []

        for cs_file in self.codebase_path.rglob(file_pattern):
            try:
                with open(cs_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    lines = content.split('\n')

                # Search for pattern keywords in content
                matching_lines = []
                for i, line in enumerate(lines, 1):
                    line_lower = line.lower()
                    # Check if any keyword is in the line
                    if any(keyword in line_lower for keyword in pattern_keywords):
                        matching_lines.append({
                            "line_number": i,
                            "content": line.strip()
                        })

                if matching_lines:
                    matches.append({
                        "file": str(cs_file.relative_to(self.codebase_path)),
                        "matches": matching_lines[:10]  # Limit to first 10 matches
                    })

            except Exception:
                continue

        return matches

    def get_class_structure(self, file_path: str) -> Dict[str, Any]:
        """
        Extract structure from a C# file

        Args:
            file_path: Path to the C# file (relative or absolute)

        Returns:
            Class structure with namespaces, classes, members
        """
        file_path = Path(file_path)
        if not file_path.is_absolute():
            file_path = self.codebase_path / file_path

        if not file_path.exists():
            return {"error": f"File not found: {file_path}"}

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Extract namespace
        ns_match = re.search(r'namespace\s+([\w.]+)', content)
        namespace = ns_match.group(1) if ns_match else None

        # Extract all type definitions
        type_matches = re.finditer(
            r'(public|internal|private|protected)?\s*(abstract|sealed|static)?\s*(class|interface|struct|record)\s+(\w+)',
            content
        )

        structures = []
        for match in type_matches:
            struct_type = match.group(2) or ""  # abstract/sealed/static
            kind = match.group(3)  # class/interface/struct
            name = match.group(4)

            # Extract members (simplified)
            members = re.findall(
                rf'(public|internal|protected|private)\s+(?:override\s+)?(\w+(?:<[^>]+>)?)\s*\([^)]*\)\s*{{?',
                content
            )

            structures.append({
                "type": struct_type,
                "kind": kind,
                "name": name,
                "members": [m[1] for m in members]
            })

        return {
            "file": str(file_path.relative_to(self.codebase_path)),
            "namespace": namespace,
            "structures": structures
        }

    def find_class_usages(self, class_name: str) -> Dict[str, Any]:
        """
        Find all usages of a class or interface

        Args:
            class_name: Class or interface name

        Returns:
            Usage information with files and locations
        """
        usages = []

        for cs_file in self.codebase_path.rglob("*.cs"):
            try:
                with open(cs_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    lines = content.split('\n')

                # Look for class being used
                for i, line in enumerate(lines, 1):
                    if re.search(rf'\b{re.escape(class_name)}\b', line):
                        # Determine usage type
                        usage_type = "unknown"
                        if f": {class_name}" in line or f"<{class_name}>" in line:
                            usage_type = "inheritance"
                        elif f"new {class_name}" in line:
                            usage_type = "instantiation"
                        elif f" {class_name} " in line or f"({class_name} " in line:
                            usage_type = "reference"

                        usages.append({
                            "file": str(cs_file.relative_to(self.codebase_path)),
                            "line": i,
                            "type": usage_type,
                            "context": lines[max(0, i-2):min(len(lines), i+1)][0] if i > 0 else line
                        })

            except Exception:
                continue

        # Group by file
        by_file = {}
        for usage in usages:
            file = usage["file"]
            if file not in by_file:
                by_file[file] = []
            by_file[file].append(usage)

        return {
            "class_name": class_name,
            "total_usages": len(usages),
            "files_affected": len(by_file),
            "by_file": by_file
        }

    def find_implementations(self, interface_name: str) -> List[Dict[str, Any]]:
        """
        Find all implementations of an interface or abstract class

        Args:
            interface_name: Interface or abstract class name

        Returns:
            List of implementing classes with details
        """
        implementations = []

        for cs_file in self.codebase_path.rglob("*.cs"):
            try:
                with open(cs_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                    # Look for inheritance
                    if re.search(rf':\s*{re.escape(interface_name)}\b', content):
                        # Extract class name
                        class_match = re.search(r'class\s+(\w+)', content)
                        if class_match and class_match.group(1) != interface_name:
                            impl_class = class_match.group(1)

                            # Extract methods
                            methods = re.findall(
                                rf'(public|internal|protected)\s+(?:override\s+)?(\w+(?:<[^>]+>)?)\s*\(',
                                content
                            )

                            implementations.append({
                                "class": impl_class,
                                "file": str(cs_file.relative_to(self.codebase_path)),
                                "methods": [m[1] for m in methods[:10]]
                            })
            except Exception:
                continue

        return implementations
