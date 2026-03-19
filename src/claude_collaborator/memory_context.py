"""
Context Tracker for Intelligent Context Management
Tracks context usage and handles semantic offloading
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .memory_vector import VectorStore


class ContextItem:
    """A single item in the working context"""

    def __init__(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        item_type: str = "general"
    ):
        self.id = str(uuid.uuid4())
        self.content = content
        self.metadata = metadata or {}
        self.item_type = item_type
        self.created_at = datetime.now()
        self.access_count = 1
        self.last_accessed = self.created_at

    def touch(self):
        """Update access time and count"""
        self.access_count += 1
        self.last_accessed = datetime.now()

    def age_seconds(self) -> int:
        """Get age of item in seconds"""
        return int((datetime.now() - self.created_at).total_seconds())

    def relevance_score(self, query_embedding=None, vector_store=None) -> float:
        """
        Calculate relevance score for this item

        Combines:
        - Recency bonus (more recent = higher)
        - Access frequency bonus
        - Semantic similarity (if query embedding provided)
        """
        score = 0.0

        # Recency bonus (0-0.3)
        # Items from last 5 minutes get full bonus
        age = self.age_seconds()
        if age < 300:
            score += 0.3 * (1 - age / 300)

        # Access frequency bonus (0-0.2)
        # More accesses = higher bonus, capped at 10
        score += min(0.2, self.access_count * 0.02)

        # Semantic similarity (0-0.5) - most important
        if query_embedding is not None and vector_store is not None:
            # Compute embedding for this item's content
            embedding = vector_store._compute_embedding(self.content[:500])  # Truncate for perf
            if embedding is not None:
                similarity = vector_store._cosine_similarity(query_embedding, embedding)
                score += similarity * 0.5

        return score


class ContextTracker:
    """
    Track context usage and trigger intelligent offloading

    Manages working memory and offloads low-relevance context
    to persistent storage when approaching limits.
    """

    def __init__(
        self,
        vector_store: VectorStore,
        threshold_chars: int = 50000,
        offload_ratio: float = 0.3
    ):
        """
        Initialize context tracker

        Args:
            vector_store: VectorStore for semantic operations
            threshold_chars: Context size threshold for offload trigger
            offload_ratio: Ratio of context to offload when threshold reached (0-1)
        """
        self.vector_store = vector_store
        self.threshold = threshold_chars
        self.offload_ratio = offload_ratio

        # Working context
        self.context_items: List[ContextItem] = []

        # Offloaded context (stored summaries)
        self.offloaded_items: List[Dict[str, Any]] = []

    @property
    def current_size(self) -> int:
        """Get current context size in characters"""
        return sum(len(item.content) for item in self.context_items)

    def add_context(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        item_type: str = "general"
    ) -> str:
        """
        Add context to working memory

        Args:
            content: Context content
            metadata: Optional metadata
            item_type: Type of context (general, file_read, tool_result, etc.)

        Returns:
            Context ID
        """
        item = ContextItem(content, metadata, item_type)
        self.context_items.append(item)

        # Check if we need to offload
        if self.current_size > self.threshold:
            self._trigger_offload()

        return item.id

    def get_context(self, context_id: str) -> Optional[ContextItem]:
        """Get a specific context item by ID"""
        for item in self.context_items:
            if item.id == context_id:
                item.touch()
                return item
        return None

    def get_current_context(self, max_chars: Optional[int] = None) -> str:
        """
        Get current working context as string

        Args:
            max_chars: Optional limit on characters returned

        Returns:
            Concatenated context content
        """
        # Sort by relevance (recent + frequently accessed)
        sorted_items = sorted(
            self.context_items,
            key=lambda x: (x.last_accessed, x.access_count),
            reverse=True
        )

        content = "\n\n".join(item.content for item in sorted_items)

        if max_chars and len(content) > max_chars:
            content = content[:max_chars] + "\n\n... [context truncated]"

        return content

    def _trigger_offload(self, current_query: str = "") -> Dict[str, Any]:
        """
        Offload low-relevance context to vector store

        Args:
            current_query: Current work query for relevance scoring

        Returns:
            Summary of what was offloaded
        """
        if not self.vector_store._check_embedding_available():
            # No embeddings available, offload oldest items
            return self._offload_by_recency()

        # Compute query embedding if provided
        query_embedding = None
        if current_query:
            query_embedding = self.vector_store._compute_embedding(current_query)

        # Score all items by relevance
        scored_items = [
            (item, item.relevance_score(query_embedding, self.vector_store))
            for item in self.context_items
        ]

        # Sort by relevance (lowest first)
        scored_items.sort(key=lambda x: x[1])

        # Determine how many to offload
        target_size = self.threshold * (1 - self.offload_ratio)
        offloaded_count = 0
        offloaded_size = 0
        offloaded_ids = []

        # Offload lowest relevance items
        remaining_items = []
        for item, score in scored_items:
            if offloaded_size < (self.current_size - target_size):
                # Offload this item
                offloaded_ids.append(item.id)
                offloaded_count += 1
                offloaded_size += len(item.content)

                # Store in vector store for potential retrieval
                self._offload_to_storage(item, score)
            else:
                remaining_items.append(item)

        self.context_items = remaining_items

        return {
            "offloaded_count": offloaded_count,
            "offloaded_size": offloaded_size,
            "remaining_size": self.current_size,
            "offloaded_ids": offloaded_ids
        }

    def _offload_by_recency(self) -> Dict[str, Any]:
        """Offload oldest items when no embeddings available"""
        # Sort by creation time (oldest first)
        sorted_items = sorted(self.context_items, key=lambda x: x.created_at)

        target_size = self.threshold * (1 - self.offload_ratio)
        offloaded_size = 0
        offloaded_ids = []
        remaining_items = []

        for item in sorted_items:
            if offloaded_size < (self.current_size - target_size):
                offloaded_ids.append(item.id)
                offloaded_size += len(item.content)
                self._offload_to_storage(item, 0)
            else:
                remaining_items.append(item)

        self.context_items = remaining_items

        return {
            "offloaded_count": len(offloaded_ids),
            "offloaded_size": offloaded_size,
            "remaining_size": self.current_size,
            "offloaded_ids": offloaded_ids
        }

    def _offload_to_storage(self, item: ContextItem, relevance_score: float):
        """Store offloaded item in vector store"""
        summary = self._create_summary(item)

        self.vector_store.add(
            topic=f"offloaded:{item.item_type}",
            content=summary,
            category="offloaded_context",
            metadata={
                "original_id": item.id,
                "item_type": item.item_type,
                "relevance_score": relevance_score,
                "offloaded_at": datetime.now().isoformat(),
                "original_metadata": item.metadata
            }
        )

        # Track in offloaded list
        self.offloaded_items.append({
            "id": item.id,
            "item_type": item.item_type,
            "offloaded_at": datetime.now().isoformat(),
            "relevance_score": relevance_score
        })

    def _create_summary(self, item: ContextItem) -> str:
        """Create a summary of offloaded content"""
        content = item.content

        # If content is short, return as-is
        if len(content) < 500:
            return content

        # Otherwise, truncate intelligently
        lines = content.split("\n")

        # Keep first and last few lines
        if len(lines) > 20:
            keep = 8
            summary_lines = lines[:keep] + ["\n... [summary truncated] ...\n"] + lines[-keep:]
            return "\n".join(summary_lines)

        # Just truncate to first 1000 chars
        return content[:1000] + "\n... [truncated]"

    def retrieve_relevant(
        self,
        query: str,
        limit: int = 3,
        include_offloaded: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant context for a query

        Args:
            query: Search query
            limit: Maximum results
            include_offloaded: Whether to search offloaded context

        Returns:
            List of relevant context items
        """
        results = []

        if include_offloaded and self.vector_store._check_embedding_available():
            # Search vector store for offloaded context
            vector_results = self.vector_store.search(
                query=query,
                limit=limit,
                category="offloaded_context"
            )
            results.extend(vector_results)

        # Also search current context
        query_embedding = None
        if self.vector_store._check_embedding_available():
            query_embedding = self.vector_store._compute_embedding(query)

        for item in self.context_items:
            if query_embedding is not None:
                score = item.relevance_score(query_embedding, self.vector_store)
            else:
                score = 0.0

            results.append({
                "id": item.id,
                "content": item.content[:500] + "..." if len(item.content) > 500 else item.content,
                "metadata": item.metadata,
                "item_type": item.item_type,
                "score": score,
                "source": "working_memory"
            })

        # Sort by score and limit
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:limit]

    def clear_old(self, age_seconds: int = 3600) -> int:
        """
        Clear context items older than specified age

        Args:
            age_seconds: Age threshold in seconds (default: 1 hour)

        Returns:
            Number of items cleared
        """
        cutoff = datetime.now() - timedelta(seconds=age_seconds)

        before_count = len(self.context_items)
        self.context_items = [
            item for item in self.context_items
            if item.created_at > cutoff
        ]

        return before_count - len(self.context_items)

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about context tracking"""
        # Count by type
        type_counts = {}
        for item in self.context_items:
            type_counts[item.item_type] = type_counts.get(item.item_type, 0) + 1

        return {
            "current_size": self.current_size,
            "item_count": len(self.context_items),
            "threshold": self.threshold,
            "utilization": self.current_size / self.threshold if self.threshold > 0 else 0,
            "offloaded_count": len(self.offloaded_items),
            "items_by_type": type_counts
        }
