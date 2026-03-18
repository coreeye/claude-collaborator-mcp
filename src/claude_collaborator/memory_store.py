"""
Memory Store for Codebase Knowledge
Persistent storage and retrieval of codebase information
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class MemoryStore:
    """Manages persistent memory for codebase knowledge"""

    def __init__(self, codebase_path: str):
        """
        Initialize memory store

        Args:
            codebase_path: Path to the BoneXpert codebase
        """
        self.codebase_path = Path(codebase_path)
        self.memory_path = self.codebase_path / ".codebase-memory"
        self.index_file = self.memory_path / "index.json"
        self.index = self._load_index()

    def _load_index(self) -> Dict[str, Any]:
        """Load or create memory index"""
        if self.index_file.exists():
            with open(self.index_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {
                "created": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "topics": {},
                "findings": [],
                "tasks": {}
            }

    def _save_index(self):
        """Save memory index to disk"""
        self.index["last_updated"] = datetime.now().isoformat()
        self.memory_path.mkdir(parents=True, exist_ok=True)

        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(self.index, f, indent=2, ensure_ascii=False)

    def save_finding(
        self,
        topic: str,
        content: str,
        category: str = "findings",
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Save a finding to memory

        Args:
            topic: Topic name (e.g., "BXCore", "DICOM handling")
            content: The finding content (markdown or text)
            category: Category (architecture, testing, findings, etc.)
            metadata: Additional metadata (source files, timestamp, etc.)

        Returns:
            Path to saved file
        """
        # Create category directory
        category_path = self.memory_path / category
        category_path.mkdir(parents=True, exist_ok=True)

        # Create filename
        safe_topic = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in topic)
        filename = f"{safe_topic}.md"
        file_path = category_path / filename

        # Prepare content with metadata header
        metadata_header = ""
        if metadata:
            metadata_header = "---\n"
            for key, value in metadata.items():
                metadata_header += f"{key}: {value}\n"
            metadata_header += "---\n\n"

        # Write content
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(metadata_header)
            f.write(content)

        # Update index
        if category not in self.index["topics"]:
            self.index["topics"][category] = {}

        self.index["topics"][category][topic] = {
            "file": str(file_path.relative_to(self.codebase_path)),
            "created": datetime.now().isoformat(),
            "metadata": metadata or {}
        }

        self._save_index()

        return str(file_path)

    def get_topic(self, topic: str, category: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieve a topic from memory

        Args:
            topic: Topic name
            category: Category to search in (or all categories if None)

        Returns:
            Topic info with content, or None if not found
        """
        # Search in specific category or all categories
        categories = [category] if category else self.index["topics"].keys()

        for cat in categories:
            if cat in self.index["topics"] and topic in self.index["topics"][cat]:
                topic_info = self.index["topics"][cat][topic]
                file_path = self.codebase_path / topic_info["file"]

                if file_path.exists():
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    return {
                        "topic": topic,
                        "category": cat,
                        "content": content,
                        "created": topic_info["created"],
                        "metadata": topic_info.get("metadata", {})
                    }

        return None

    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Search memory for query

        Args:
            query: Search query

        Returns:
            List of matching topics with snippets
        """
        results = []
        query_lower = query.lower()

        # Search through all topics
        for category, topics in self.index["topics"].items():
            for topic, topic_info in topics.items():
                file_path = self.codebase_path / topic_info["file"]

                if file_path.exists():
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # Search in topic name and content
                    if query_lower in topic.lower() or query_lower in content.lower():
                        # Get snippet (first 200 chars containing query)
                        lines = content.split('\n')
                        snippet = ""
                        for i, line in enumerate(lines):
                            if query_lower in line.lower():
                                start = max(0, i - 2)
                                end = min(len(lines), i + 3)
                                snippet = '\n'.join(lines[start:end])
                                snippet = snippet[:200] + "..." if len(snippet) > 200 else snippet
                                break

                        results.append({
                            "topic": topic,
                            "category": category,
                            "snippet": snippet or content[:200],
                            "created": topic_info["created"]
                        })

        return results

    def get_status(self) -> Dict[str, Any]:
        """
        Get memory store status

        Returns:
            Status info including topics, counts, etc.
        """
        topic_counts = {}
        for category, topics in self.index["topics"].items():
            topic_counts[category] = len(topics)

        return {
            "memory_path": str(self.memory_path),
            "created": self.index["created"],
            "last_updated": self.index["last_updated"],
            "total_topics": sum(len(topics) for topics in self.index["topics"].values()),
            "topics_by_category": topic_counts
        }

    def import_from_markdown(self, file_path: str, category: str = "architecture") -> bool:
        """
        Import knowledge from a markdown file

        Args:
            file_path: Path to markdown file
            category: Category to save under

        Returns:
            True if successful
        """
        file_path = Path(file_path)

        if not file_path.exists():
            return False

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Use filename (without .md) as topic
        topic = file_path.stem

        self.save_finding(
            topic=topic,
            content=content,
            category=category,
            metadata={
                "source": str(file_path),
                "imported": datetime.now().isoformat()
            }
        )

        return True
