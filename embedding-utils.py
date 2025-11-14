#!/usr/bin/env python3
"""
Local Embedding Utilities for Tag Similarity
Uses Hugging Face sentence-transformers for local embedding generation
Redis Stack vector search for fast similarity lookups
"""

import json
import numpy as np
import redis
import struct
from sentence_transformers import SentenceTransformer
from typing import List, Tuple, Dict, Optional
import os

# Redis connection - use decode_responses=False for binary data handling
r = redis.Redis(host='localhost', port=16379, decode_responses=False)

class LocalEmbeddingManager:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize with a lightweight, fast sentence transformer model.
        all-MiniLM-L6-v2: 22MB, good for semantic similarity tasks
        """
        print(f"Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.model_name = model_name

    def get_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using local Hugging Face model."""
        try:
            # Get embedding as numpy array
            embedding = self.model.encode(text, convert_to_numpy=True)
            # Convert to list for JSON serialization
            return embedding.tolist()
        except Exception as e:
            print(f"Error generating embedding for '{text}': {e}")
            return None

    def store_tag_embedding(self, tag: str, embedding: List[float] = None) -> bool:
        """Store tag embedding in Redis using binary format for vector search."""
        if embedding is None:
            embedding = self.get_embedding(tag)

        if embedding is None:
            return False

        try:
            # Convert embedding to binary format (float32)
            vector_bytes = struct.pack(f'{len(embedding)}f', *embedding)

            # Store binary embedding for vector search
            key = f"tag_embeddings:{tag}".encode('utf-8')
            r.hset(key, b"embedding", vector_bytes)
            r.hset(key, b"model", self.model_name.encode('utf-8'))
            r.hset(key, b"dimensions", str(len(embedding)).encode('utf-8'))
            return True
        except Exception as e:
            print(f"Error storing embedding for '{tag}': {e}")
            return False

    def get_stored_embedding(self, tag: str) -> Optional[List[float]]:
        """Retrieve stored embedding for a tag."""
        try:
            key = f"tag_embeddings:{tag}".encode('utf-8')
            embedding_bytes = r.hget(key, b"embedding")
            if not embedding_bytes:
                return None

            # Unpack binary float32 array
            num_floats = len(embedding_bytes) // 4
            embedding = list(struct.unpack(f'{num_floats}f', embedding_bytes))
            return embedding
        except Exception as e:
            print(f"Error retrieving embedding for '{tag}': {e}")
            return None

    def find_similar_tags(self, tag: str, threshold: float = 0.7, max_results: int = 5) -> List[Tuple[str, float]]:
        """
        Find existing tags with high semantic similarity using Redis vector search.

        Args:
            tag: Tag to find similarities for
            threshold: Minimum similarity score (0.7 = fairly similar)
            max_results: Maximum number of results to return

        Returns:
            List of (tag_name, similarity_score) tuples, sorted by similarity desc
        """
        # Get or generate embedding for input tag
        query_embedding = self.get_stored_embedding(tag)
        if query_embedding is None:
            query_embedding = self.get_embedding(tag)
            if query_embedding is None:
                return []

        # Convert query embedding to binary format
        vector_bytes = struct.pack(f'{len(query_embedding)}f', *query_embedding)

        # Use Redis vector search with KNN
        # Search for top N candidates (more than max_results to allow for filtering)
        search_limit = max(max_results * 3, 20)

        from redis.commands.search.query import Query

        # KNN query with COSINE distance
        query = (
            Query(f"*=>[KNN {search_limit} @embedding $vec AS distance]")
            .sort_by("distance")
            .return_fields("distance")
            .dialect(2)
        )

        results = r.ft("tag_idx").search(query, query_params={"vec": vector_bytes})

        similar_tags = []
        for doc in results.docs:
            # Extract tag name from document id (format: tag_embeddings:tagname)
            doc_id = doc.id.decode('utf-8') if isinstance(doc.id, bytes) else doc.id
            existing_tag = doc_id.replace("tag_embeddings:", "")

            # Skip the query tag itself
            if existing_tag == tag:
                continue

            # Convert COSINE distance to similarity (1 - distance for COSINE)
            distance = float(doc.distance)
            similarity = 1.0 - distance

            # Filter by threshold
            if similarity >= threshold:
                # Get usage count for ranking
                usage_key = f"tag_usage:{existing_tag}".encode('utf-8')
                count_bytes = r.hget(usage_key, b"count")
                usage_count = int(count_bytes.decode('utf-8')) if count_bytes else 0
                similar_tags.append((existing_tag, similarity, usage_count))

        # Sort by similarity score descending, then by usage count
        similar_tags.sort(key=lambda x: (x[1], x[2]), reverse=True)

        # Return just tag name and similarity score
        return [(tag_name, sim) for tag_name, sim, _ in similar_tags[:max_results]]

    def suggest_tag_merges(self, similarity_threshold: float = 0.9) -> List[Tuple[str, str, float]]:
        """
        Find pairs of tags that are very similar and could be merged using Redis vector search.

        Args:
            similarity_threshold: Minimum similarity for merge suggestion (0.9 = very similar)

        Returns:
            List of (tag1, tag2, similarity) tuples for potential merges
        """
        merge_candidates = []
        seen_pairs = set()  # Track (tag1, tag2) pairs to avoid duplicates

        # Get all existing tags
        all_tags = []
        for key in r.scan_iter(match=b"tag_embeddings:*"):
            tag = key.decode('utf-8').replace("tag_embeddings:", "")
            all_tags.append(tag)

        # For each tag, use Redis vector search to find similar tags
        for tag in all_tags:
            # Use Redis vector search to find similar tags above threshold
            similar_tags = self.find_similar_tags(tag, threshold=similarity_threshold, max_results=10)

            for similar_tag, similarity in similar_tags:
                # Create sorted pair to avoid duplicate comparisons
                pair = tuple(sorted([tag, similar_tag]))
                if pair in seen_pairs:
                    continue

                seen_pairs.add(pair)

                # Get usage counts to determine which tag to keep
                count1_bytes = r.hget(f"tag_usage:{tag}".encode('utf-8'), b"count")
                count2_bytes = r.hget(f"tag_usage:{similar_tag}".encode('utf-8'), b"count")
                count1 = int(count1_bytes.decode('utf-8')) if count1_bytes else 0
                count2 = int(count2_bytes.decode('utf-8')) if count2_bytes else 0

                # Suggest keeping the more frequently used tag
                if count1 >= count2:
                    merge_candidates.append((similar_tag, tag, similarity))  # merge similar_tag into tag
                else:
                    merge_candidates.append((tag, similar_tag, similarity))  # merge tag into similar_tag

        # Sort by similarity descending
        merge_candidates.sort(key=lambda x: x[2], reverse=True)
        return merge_candidates

    def generate_embeddings_for_existing_tags(self):
        """Generate embeddings for all existing tags that don't have them."""
        print("Generating embeddings for existing tags...")

        # Get all existing tags from usage tracking
        existing_tags = set()
        for key in r.scan_iter(match=b"tag_usage:*"):
            tag = key.decode('utf-8').replace("tag_usage:", "")
            existing_tags.add(tag)

        generated_count = 0
        for tag in existing_tags:
            # Check if embedding already exists
            if self.get_stored_embedding(tag) is None:
                print(f"Generating embedding for: {tag}")
                if self.store_tag_embedding(tag):
                    generated_count += 1
                else:
                    print(f"Failed to generate embedding for: {tag}")

        print(f"Generated embeddings for {generated_count} tags")
        return generated_count


def main():
    """CLI interface for embedding utilities."""
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  embedding-utils.py generate-all    # Generate embeddings for all existing tags")
        print("  embedding-utils.py similar <tag>   # Find similar tags")
        print("  embedding-utils.py merges          # Find potential tag merges")
        sys.exit(1)

    manager = LocalEmbeddingManager()
    command = sys.argv[1]

    if command == "generate-all":
        manager.generate_embeddings_for_existing_tags()

    elif command == "similar" and len(sys.argv) > 2:
        tag = sys.argv[2]
        similar_tags = manager.find_similar_tags(tag, threshold=0.55)

        print(f"\nTags similar to '{tag}':")
        for similar_tag, similarity in similar_tags:
            print(f"  {similar_tag}: {similarity:.3f}")

    elif command == "merges":
        merge_candidates = manager.suggest_tag_merges(similarity_threshold=0.6)

        print("\nPotential tag merges:")
        for tag1, tag2, similarity in merge_candidates:
            print(f"  '{tag1}' → '{tag2}' (similarity: {similarity:.3f})")

    else:
        print("Invalid command. Use 'generate-all', 'similar <tag>', or 'merges'")
        sys.exit(1)


if __name__ == "__main__":
    main()
