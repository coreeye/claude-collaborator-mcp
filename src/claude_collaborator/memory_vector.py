"""
Vector Memory Store for Semantic Similarity Search
Provides embedding-based semantic search capabilities
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np


class VectorStore:
    """
    Vector storage for semantic similarity search.

    Stores embeddings in SQLite for persistent semantic memory.
    Gracefully degrades if sentence-transformers is not installed.
    """

    def __init__(self, codebase_path: str, embedding_model: str = "all-MiniLM-L6-v2"):
        """
        Initialize vector store

        Args:
            codebase_path: Path to the codebase
            embedding_model: Name of the sentence-transformers model
        """
        self.codebase_path = Path(codebase_path)
        self.memory_path = self.codebase_path / ".codebase-memory"
        self.db_path = self.memory_path / "vectors.db"
        self.embedding_model_name = embedding_model

        # Lazy-load embedding model
        self._embedding_model = None
        self._embedding_available = None

        # Initialize database
        self._init_db()

    def _check_embedding_available(self) -> bool:
        """Check if sentence-transformers is available"""
        if self._embedding_available is not None:
            return self._embedding_available

        try:
            from sentence_transformers import SentenceTransformer
            self._embedding_available = True
            return True
        except ImportError:
            self._embedding_available = False
            return False

    def _get_embedding_model(self):
        """Get or lazy-load the embedding model"""
        if not self._check_embedding_available():
            return None

        if self._embedding_model is None:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer(self.embedding_model_name)

        return self._embedding_model

    def _init_db(self):
        """Initialize SQLite database with vectors table"""
        self.memory_path.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create vectors table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vectors (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT NOT NULL,
                metadata_json TEXT,
                embedding BLOB,
                created_at TEXT NOT NULL
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_category
            ON vectors(category)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_created
            ON vectors(created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_topic
            ON vectors(topic)
        """)

        conn.commit()
        conn.close()

    def _embedding_to_blob(self, embedding: np.ndarray) -> bytes:
        """Convert numpy array to SQLite BLOB"""
        return embedding.astype(np.float32).tobytes()

    def _blob_to_embedding(self, blob: bytes) -> np.ndarray:
        """Convert SQLite BLOB to numpy array"""
        return np.frombuffer(blob, dtype=np.float32)

    def _compute_embedding(self, text: str) -> Optional[np.ndarray]:
        """Compute embedding for text"""
        model = self._get_embedding_model()
        if model is None:
            return None

        return model.encode(text, convert_to_numpy=True)

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Compute cosine similarity between two vectors"""
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(np.dot(vec1, vec2) / (norm1 * norm2))

    def add(
        self,
        topic: str,
        content: str,
        category: str = "findings",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Add content with embedding to vector store

        Args:
            topic: Topic name
            content: Content to store
            category: Category for organization
            metadata: Additional metadata

        Returns:
            Vector ID if successful, None if embeddings unavailable
        """
        if not self._check_embedding_available():
            return None

        # Generate unique ID
        vector_id = str(uuid.uuid4())

        # Compute embedding
        embedding = self._compute_embedding(f"{topic}. {content}")
        if embedding is None:
            return None

        # Prepare metadata
        metadata_json = json.dumps(metadata or {})

        # Insert into database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO vectors (id, topic, content, category, metadata_json, embedding, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            vector_id,
            topic,
            content,
            category,
            metadata_json,
            self._embedding_to_blob(embedding),
            datetime.now().isoformat()
        ))

        conn.commit()
        conn.close()

        return vector_id

    def search(
        self,
        query: str,
        limit: int = 5,
        category: Optional[str] = None,
        min_score: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Semantic similarity search

        Args:
            query: Search query in natural language
            limit: Maximum number of results
            category: Filter by category (optional)
            min_score: Minimum similarity score (0-1)

        Returns:
            List of results with similarity scores
        """
        if not self._check_embedding_available():
            return []

        # Compute query embedding
        query_embedding = self._compute_embedding(query)
        if query_embedding is None:
            return []

        # Query database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        sql = "SELECT id, topic, content, category, metadata_json, embedding FROM vectors"
        params = []

        if category:
            sql += " WHERE category = ?"
            params.append(category)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()

        # Compute similarities and rank
        results = []
        for row in rows:
            vector_id, topic, content, cat, metadata_json, embedding_blob = row
            embedding = self._blob_to_embedding(embedding_blob)

            score = self._cosine_similarity(query_embedding, embedding)

            if score >= min_score:
                results.append({
                    "id": vector_id,
                    "topic": topic,
                    "content": content,
                    "category": cat,
                    "metadata": json.loads(metadata_json) if metadata_json else {},
                    "score": score
                })

        # Sort by score descending and limit
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def get_by_id(self, vector_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve specific entry by ID

        Args:
            vector_id: The vector entry ID

        Returns:
            Entry data or None if not found
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, topic, content, category, metadata_json, created_at
            FROM vectors WHERE id = ?
        """, (vector_id,))

        row = cursor.fetchone()
        conn.close()

        if row is None:
            return None

        vector_id, topic, content, category, metadata_json, created_at = row

        return {
            "id": vector_id,
            "topic": topic,
            "content": content,
            "category": category,
            "metadata": json.loads(metadata_json) if metadata_json else {},
            "created_at": created_at
        }

    def delete(self, vector_id: str) -> bool:
        """
        Remove entry from vector store

        Args:
            vector_id: The vector entry ID to delete

        Returns:
            True if deleted, False if not found
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM vectors WHERE id = ?", (vector_id,))
        affected = cursor.rowcount

        conn.commit()
        conn.close()

        return affected > 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the vector store

        Returns:
            Statistics including count, categories, model info
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Total count
        cursor.execute("SELECT COUNT(*) FROM vectors")
        total = cursor.fetchone()[0]

        # Count by category
        cursor.execute("""
            SELECT category, COUNT(*) as count
            FROM vectors
            GROUP BY category
        """)
        categories = dict(cursor.fetchall())

        conn.close()

        return {
            "db_path": str(self.db_path),
            "total_entries": total,
            "categories": categories,
            "embedding_model": self.embedding_model_name,
            "embeddings_available": self._check_embedding_available()
        }

    def list_by_category(self, category: str) -> List[Dict[str, Any]]:
        """
        List all entries in a category

        Args:
            category: Category name

        Returns:
            List of entries in the category
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, topic, content, created_at
            FROM vectors
            WHERE category = ?
            ORDER BY created_at DESC
        """, (category,))

        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "id": row[0],
                "topic": row[1],
                "content": row[2],
                "created_at": row[3]
            }
            for row in rows
        ]
