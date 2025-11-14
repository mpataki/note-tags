#!/usr/bin/env python3
"""
Reseed tag data in Redis from markdown files.

Recursively scans a directory for markdown files, extracts tags from frontmatter,
and rebuilds Redis tag tracking data. Use this after flushing Redis or when
improving tagging standards.

Usage:
    reseed-tags.py <directory>              # Reseed from directory
    reseed-tags.py <directory> --flush      # Flush Redis first, then reseed
"""

import sys
import json
import redis
from pathlib import Path
from typing import List, Dict, Set
from collections import defaultdict
import importlib.util

# Dynamically import modules from .claude directory
def load_module(module_name, filepath):
    """Load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Get script directory
SCRIPT_DIR = Path(__file__).parent

# Load apply_tags module for frontmatter reading
apply_tags = load_module("apply_tags", SCRIPT_DIR / "apply-tags.py")
read_file_with_frontmatter = apply_tags.read_file_with_frontmatter

# Redis connection - decode_responses=False for binary vector data
r = redis.Redis(host='localhost', port=16379, decode_responses=False)


def find_markdown_files(directory: Path) -> List[Path]:
    """Recursively find all markdown files in directory."""
    return list(directory.rglob("*.md"))


def extract_tags_from_file(filepath: Path) -> tuple[str, List[str]]:
    """
    Extract note ID and tags from a markdown file.

    Returns:
        (note_id, tags) tuple
    """
    frontmatter, _ = read_file_with_frontmatter(str(filepath))

    note_id = frontmatter.get('id')
    if not note_id:
        # Generate from filename if missing
        note_id = filepath.stem.replace(' ', '-').lower()

    tags = frontmatter.get('tags', [])

    return note_id, tags


def build_tag_data(directory: Path, verbose: bool = True) -> Dict:
    """
    Scan directory and build tag tracking data structure.

    Returns:
        {
            'tag_usage': {tag: {'count': int, 'notes': [note_ids]}},
            'note_tags': {note_id: [tags]}
        }
    """
    if verbose:
        print(f"Scanning directory: {directory}")

    markdown_files = find_markdown_files(directory)

    if verbose:
        print(f"Found {len(markdown_files)} markdown files")

    tag_usage = defaultdict(lambda: {'count': 0, 'notes': []})
    note_tags = {}

    for filepath in markdown_files:
        try:
            note_id, tags = extract_tags_from_file(filepath)

            if not tags:
                continue

            # Store note's tags
            note_tags[note_id] = tags

            # Update tag usage
            for tag in tags:
                tag_usage[tag]['count'] += 1
                if note_id not in tag_usage[tag]['notes']:
                    tag_usage[tag]['notes'].append(note_id)

            if verbose:
                print(f"  {filepath.name}: {note_id} → {len(tags)} tags")

        except Exception as e:
            print(f"Error processing {filepath}: {e}", file=sys.stderr)
            continue

    return {
        'tag_usage': dict(tag_usage),
        'note_tags': note_tags
    }


def flush_redis_tags(verbose: bool = True):
    """Delete all tag-related keys from Redis."""
    if verbose:
        print("\nFlushing existing tag data from Redis...")

    # Delete tag usage keys
    for key in r.scan_iter(match=b"tag_usage:*"):
        r.delete(key)

    # Delete tag embeddings keys
    for key in r.scan_iter(match=b"tag_embeddings:*"):
        r.delete(key)

    if verbose:
        print("Redis tag data flushed")


def update_redis(tag_data: Dict, verbose: bool = True):
    """Update Redis with tag tracking data."""
    if verbose:
        print(f"\nUpdating Redis with tag data...")

    tag_usage = tag_data['tag_usage']

    # Update tag usage
    for tag, data in tag_usage.items():
        key = f"tag_usage:{tag}".encode('utf-8')
        r.hset(key, b"count", str(data['count']).encode('utf-8'))
        r.hset(key, b"notes", json.dumps(data['notes']).encode('utf-8'))

    if verbose:
        print(f"Stored {len(tag_usage)} unique tags")


def generate_embeddings(tag_data: Dict, verbose: bool = True):
    """Generate embeddings for all tags."""
    if verbose:
        print(f"\nGenerating embeddings for tags...")

    # Load embedding manager
    embedding_utils = load_module("embedding_utils", SCRIPT_DIR / "embedding-utils.py")
    manager = embedding_utils.LocalEmbeddingManager()

    tag_usage = tag_data['tag_usage']
    generated_count = 0
    failed_count = 0

    for tag in tag_usage.keys():
        try:
            if verbose:
                print(f"  Generating embedding for: {tag}")
            if manager.store_tag_embedding(tag):
                generated_count += 1
            else:
                failed_count += 1
                print(f"  Failed to generate embedding for: {tag}")
        except Exception as e:
            failed_count += 1
            print(f"  Error generating embedding for '{tag}': {e}")

    if verbose:
        print(f"\nGenerated {generated_count} embeddings")
        if failed_count > 0:
            print(f"Failed to generate {failed_count} embeddings")


def print_summary(tag_data: Dict):
    """Print summary statistics."""
    tag_usage = tag_data['tag_usage']
    note_tags = tag_data['note_tags']

    print("\n" + "="*60)
    print("TAG RESEEDING SUMMARY")
    print("="*60)

    print(f"\nTotal unique tags: {len(tag_usage)}")
    print(f"Total tagged notes: {len(note_tags)}")

    # Top 10 most used tags
    sorted_tags = sorted(tag_usage.items(), key=lambda x: x[1]['count'], reverse=True)

    print("\nTop 10 most used tags:")
    for tag, data in sorted_tags[:10]:
        print(f"  {tag}: {data['count']} notes")

    # Tag usage distribution
    usage_counts = defaultdict(int)
    for tag, data in tag_usage.items():
        usage_counts[data['count']] += 1

    print("\nTag usage distribution:")
    for count in sorted(usage_counts.keys(), reverse=True)[:5]:
        num_tags = usage_counts[count]
        print(f"  {num_tags} tags used in {count} note(s)")


def main():
    """CLI interface for tag reseeding."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  reseed-tags.py <directory>              # Reseed from directory")
        print("  reseed-tags.py <directory> --flush      # Flush Redis first, then reseed")
        print("")
        print("Examples:")
        print("  reseed-tags.py .")
        print("  reseed-tags.py . --flush")
        print("  reseed-tags.py areas/syncdna")
        sys.exit(1)

    directory = Path(sys.argv[1])
    should_flush = "--flush" in sys.argv

    # Validate directory exists
    if not directory.exists():
        print(f"Error: Directory not found: {directory}", file=sys.stderr)
        sys.exit(1)

    if not directory.is_dir():
        print(f"Error: Not a directory: {directory}", file=sys.stderr)
        sys.exit(1)

    try:
        # Flush if requested
        if should_flush:
            flush_redis_tags(verbose=True)

        # Build tag data from files
        tag_data = build_tag_data(directory, verbose=True)

        # Update Redis
        update_redis(tag_data, verbose=True)

        # Generate embeddings for all tags
        generate_embeddings(tag_data, verbose=True)

        # Print summary
        print_summary(tag_data)

        print("\n✅ Tag reseeding complete!")
        sys.exit(0)

    except Exception as e:
        print(f"Error during reseeding: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
