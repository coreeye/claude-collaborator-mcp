"""
Vector Memory Store for Semantic Similarity Search
Provides embedding-based semantic search capabilities
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

# Suppress SentenceTransformer output at import time, before any model loading.
# These env vars must be set before importing sentence_transformers.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")


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
        self._warmup_thread = None
        self._model_lock = __import__('threading').Lock()

        self._warmup_started = False

        # Initialize database
        self._init_db()

        # NOTE: Warmup is NOT started here. It must be started after the MCP
        # stdio transport has captured its sys.stdout reference, otherwise the
        # stdout redirect during model loading can corrupt the transport.
        # Call ensure_warmup_started() from the first tool dispatch.

    # Cache at module level - check once, use forever
    _ST_AVAILABLE = None
    _ST_CHECKED = False

    def _check_embedding_available(self) -> bool:
        """Check if sentence-transformers is available (cached result)"""
        # Check class-level cache first (fastest)
        if VectorStore._ST_CHECKED:
            return VectorStore._ST_AVAILABLE

        # Then check instance-level cache
        if self._embedding_available is not None:
            return self._embedding_available

        # Only check once globally
        VectorStore._ST_CHECKED = True
        try:
            # Just check if it can be imported, don't load the model
            import importlib.util
            spec = importlib.util.find_spec("sentence_transformers")
            if spec is not None:
                VectorStore._ST_AVAILABLE = True
                self._embedding_available = True
                return True
        except Exception:
            pass

        VectorStore._ST_AVAILABLE = False
        self._embedding_available = False
        return False

    def ensure_warmup_started(self):
        """Start the background warmup if not already started.
        Must be called after the MCP transport has initialized."""
        if not self._warmup_started:
            self._warmup_started = True
            self._start_warmup()

    def _start_warmup(self):
        """Pre-load embedding model in background thread to avoid first-call latency"""
        import threading

        self._model_ready = False

        def _warmup():
            try:
                self._get_embedding_model()
                self._model_ready = True
            except Exception:
                pass

        if self._check_embedding_available():
            self._warmup_thread = threading.Thread(target=_warmup, daemon=True)
            self._warmup_thread.start()

    def is_model_ready(self) -> bool:
        """Check if the embedding model has finished loading (non-blocking)."""
        return getattr(self, '_model_ready', False) or self._embedding_model is not None

    def _get_embedding_model(self):
        """Get or lazy-load the embedding model (thread-safe)"""
        if not self._check_embedding_available():
            return None

        # Use lock to prevent duplicate loading from main + warmup threads
        with self._model_lock:
            if self._embedding_model is None:
                import logging
                # Suppress logging from model loading libraries
                for name in ["sentence_transformers", "transformers",
                             "huggingface_hub", "filelock"]:
                    logging.getLogger(name).setLevel(logging.ERROR)
                # Redirect fd 1 and fd 2 to devnull during model loading.
                # SentenceTransformer prints progress and load reports directly
                # to fd 1 (C-level stdout). The MCP transport has been set up
                # (in main()) to use a dup'd fd instead of fd 1, so this
                # redirect is safe — it only catches stray library output.
                stdout_backup = os.dup(1)
                stderr_backup = os.dup(2)
                devnull_fd = os.open(os.devnull, os.O_WRONLY)
                try:
                    os.dup2(devnull_fd, 1)
                    os.dup2(devnull_fd, 2)
                    from sentence_transformers import SentenceTransformer
                    self._embedding_model = SentenceTransformer(self.embedding_model_name)
                finally:
                    os.dup2(stdout_backup, 1)
                    os.dup2(stderr_backup, 2)
                    os.close(devnull_fd)
                    os.close(stdout_backup)
                    os.close(stderr_backup)

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

        # Don't block waiting for model warmup — return None if not ready
        if not self.is_model_ready():
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

        # Don't block waiting for model warmup — return empty if not ready
        if not self.is_model_ready():
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
