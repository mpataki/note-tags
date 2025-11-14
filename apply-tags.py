#!/usr/bin/env python3
"""
Apply tags to Obsidian notes and update Redis embeddings
Handles file frontmatter editing and Redis consistency tracking
"""

import json
import sys
import re
import redis
from pathlib import Path
from typing import List, Dict, Optional
# Import embedding utilities - will be imported dynamically when needed
import importlib.util
import os

# Redis connection - decode_responses=False for binary vector data
r = redis.Redis(host='localhost', port=16379, decode_responses=False)

def read_file_with_frontmatter(filepath: str) -> tuple[Dict, str]:
    """
    Read a markdown file and separate frontmatter from content.

    Returns:
        (frontmatter_dict, content_without_frontmatter)
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading file {filepath}: {e}")
        return {}, ""

    # Check for frontmatter
    if not content.startswith('---'):
        # No frontmatter, return empty dict and full content
        return {}, content

    # Find end of frontmatter
    end_match = re.search(r'\n---\n', content[3:])
    if not end_match:
        # Malformed frontmatter, treat as no frontmatter
        return {}, content

    frontmatter_content = content[3:end_match.start() + 3]
    remaining_content = content[end_match.end() + 3:]

    # Parse YAML frontmatter (simple approach)
    frontmatter = {}
    lines = frontmatter_content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if not line or line.startswith('#'):
            i += 1
            continue

        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()

            # Handle different value types
            if value.startswith('[') and value.endswith(']'):
                # Inline array value: [item1, item2]
                value = value[1:-1]  # Remove brackets
                if value.strip():
                    frontmatter[key] = [item.strip() for item in value.split(',')]
                else:
                    frontmatter[key] = []
                i += 1
            elif not value:
                # Empty value - could be start of multi-line list
                # Check if next lines are list items (start with -)
                list_items = []
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if next_line.strip().startswith('- '):
                        # List item
                        list_items.append(next_line.strip()[2:].strip())
                        i += 1
                    elif next_line.strip().startswith('-'):
                        # List item without space after dash
                        list_items.append(next_line.strip()[1:].strip())
                        i += 1
                    elif next_line.strip() and not next_line.startswith(' '):
                        # New key, stop collecting list items
                        break
                    else:
                        i += 1

                if list_items:
                    frontmatter[key] = list_items
                else:
                    frontmatter[key] = ''
            else:
                # String value
                frontmatter[key] = value
                i += 1
        else:
            i += 1

    return frontmatter, remaining_content

def write_file_with_frontmatter(filepath: str, frontmatter: Dict, content: str):
    """Write markdown file with updated frontmatter."""

    # Build frontmatter section
    fm_lines = ['---']

    for key, value in frontmatter.items():
        if isinstance(value, list):
            if value:  # Non-empty list - use YAML list format
                fm_lines.append(f'{key}:')
                for item in value:
                    fm_lines.append(f'  - {item}')
            else:  # Empty list
                fm_lines.append(f'{key}: []')
        else:
            value_str = str(value)
            fm_lines.append(f'{key}: {value_str}')

    fm_lines.append('---')

    # Combine frontmatter and content
    full_content = '\n'.join(fm_lines) + '\n' + content

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_content)
        return True
    except Exception as e:
        print(f"Error writing file {filepath}: {e}")
        return False

def apply_tags_to_file(filepath: str, tags: List[str], note_id: Optional[str] = None, tagging_version: Optional[str] = None) -> bool:
    """
    Apply tags to a markdown file's frontmatter.

    Args:
        filepath: Path to the markdown file
        tags: List of tags to apply
        note_id: Optional note ID (will extract from frontmatter if not provided)
        tagging_version: Optional tagging agent version to record

    Returns:
        True if successful, False otherwise
    """

    # Read current file
    frontmatter, content = read_file_with_frontmatter(filepath)

    # Get or extract note ID
    if note_id is None:
        note_id = frontmatter.get('id')
        if not note_id:
            # Generate note ID from filename
            note_id = Path(filepath).stem.replace(' ', '-').lower()
            frontmatter['id'] = note_id

    # Store old tags for comparison
    old_tags = frontmatter.get('tags', [])

    # Update tags in frontmatter
    frontmatter['tags'] = tags

    # Update tagging version only if we're adding non-empty tags
    if tagging_version and tags:
        frontmatter['tagging-agent-version'] = tagging_version

    # Write updated file
    if not write_file_with_frontmatter(filepath, frontmatter, content):
        return False

    print(f"Applied tags to {filepath}:")
    print(f"  Previous: {old_tags}")
    print(f"  New: {tags}")

    return True

def update_redis_tracking(note_id: str, tags: List[str], old_tags: List[str] = None):
    """
    Update Redis with tag usage tracking and note relationships.

    Args:
        note_id: Unique identifier for the note
        tags: New tags applied to the note
        old_tags: Previous tags (for cleanup)
    """

    # Clean up old tag usage if provided
    if old_tags:
        for tag in old_tags:
            if tag not in tags:  # Tag was removed
                # Get current state before modification
                count_bytes = r.hget(f"tag_usage:{tag}".encode('utf-8'), b"count")
                current_count = int(count_bytes.decode('utf-8')) if count_bytes else 0

                notes_bytes = r.hget(f"tag_usage:{tag}".encode('utf-8'), b"notes")
                notes_str = notes_bytes.decode('utf-8') if notes_bytes else "[]"

                # Remove note from tag's note list
                try:
                    notes = json.loads(notes_str)
                    if note_id in notes:
                        notes.remove(note_id)
                except json.JSONDecodeError:
                    notes = []

                # Update or delete based on remaining usage
                if current_count > 1:
                    key = f"tag_usage:{tag}".encode('utf-8')
                    r.hset(key, b"count", str(current_count - 1).encode('utf-8'))
                    r.hset(key, b"notes", json.dumps(notes).encode('utf-8'))
                else:
                    # Last usage - clean up completely
                    r.delete(f"tag_usage:{tag}".encode('utf-8'))
                    r.delete(f"tag_embeddings:{tag}".encode('utf-8'))
                    print(f"  Removed tag '{tag}' from Redis (usage count reached 0)")

    # Update tracking for new tags
    for tag in tags:
        # Increment usage count
        usage_key = f"tag_usage:{tag}".encode('utf-8')
        count_bytes = r.hget(usage_key, b"count")
        current_count = int(count_bytes.decode('utf-8')) if count_bytes else 0
        r.hset(usage_key, b"count", str(current_count + 1).encode('utf-8'))

        # Add note to tag's note list
        notes_bytes = r.hget(usage_key, b"notes")
        notes_str = notes_bytes.decode('utf-8') if notes_bytes else "[]"
        try:
            notes = json.loads(notes_str)
            if note_id not in notes:
                notes.append(note_id)
                r.hset(usage_key, b"notes", json.dumps(notes).encode('utf-8'))
        except json.JSONDecodeError:
            # Initialize with this note
            r.hset(usage_key, b"notes", json.dumps([note_id]).encode('utf-8'))

    print(f"Updated Redis tracking for note '{note_id}' with {len(tags)} tags")

def generate_embeddings_for_tags(tags: List[str]):
    """Generate embeddings for any tags that don't have them."""
    try:
        # Dynamically import LocalEmbeddingManager
        spec = importlib.util.spec_from_file_location("embedding_utils",
            os.path.join(os.path.dirname(__file__), "embedding-utils.py"))
        embedding_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(embedding_module)

        manager = embedding_module.LocalEmbeddingManager()

        for tag in tags:
            # Check if embedding exists
            if manager.get_stored_embedding(tag) is None:
                print(f"Generating embedding for new tag: {tag}")
                if not manager.store_tag_embedding(tag):
                    print(f"Failed to generate embedding for: {tag}")
            else:
                print(f"Embedding already exists for: {tag}")
    except Exception as e:
        print(f"Warning: Could not generate embeddings: {e}")
        print("Continuing without embedding generation...")

def main():
    """CLI interface for tag application."""

    if len(sys.argv) < 3:
        print("Usage:")
        print("  apply-tags.py <filepath> '<tag1>,<tag2>,<tag3>'")
        print("  apply-tags.py <filepath> '[\"tag1\",\"tag2\",\"tag3\"]'")
        print("")
        print("Examples:")
        print("  apply-tags.py note.md 'python,automation,scripting'")
        print("  apply-tags.py note.md '[\"python\",\"automation\",\"scripting\"]'")
        sys.exit(1)

    filepath = sys.argv[1]
    tags_input = sys.argv[2]

    # Parse tags input
    if tags_input.startswith('[') and tags_input.endswith(']'):
        # JSON array format
        try:
            tags = json.loads(tags_input)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON tags: {e}")
            sys.exit(1)
    else:
        # Comma-separated format
        tags = [tag.strip() for tag in tags_input.split(',')]

    # Validate file exists
    if not Path(filepath).exists():
        print(f"File not found: {filepath}")
        sys.exit(1)

    # Get old tags for cleanup
    frontmatter, _ = read_file_with_frontmatter(filepath)
    old_tags = frontmatter.get('tags', [])
    note_id = frontmatter.get('id')

    # Apply tags to file
    if not apply_tags_to_file(filepath, tags, note_id):
        print("Failed to apply tags to file")
        sys.exit(1)

    # Generate embeddings for new tags
    generate_embeddings_for_tags(tags)

    # Update Redis tracking
    # Re-read to get the note_id that might have been added
    frontmatter, _ = read_file_with_frontmatter(filepath)
    note_id = frontmatter.get('id')

    if note_id:
        update_redis_tracking(note_id, tags, old_tags)
    else:
        print("Warning: No note ID found, skipping Redis updates")

    print(f"Successfully applied {len(tags)} tags to {filepath}")

if __name__ == "__main__":
    main()