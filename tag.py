#!/usr/bin/env python3
"""
Tagging orchestrator that coordinates the complete tagging pipeline:
1. Get tag suggestions from Claude CLI (via slash command) OR use provided tags
2. Refine suggestions by checking similarity against existing tags
3. Apply refined tags to file and update Redis tracking

Usage:
    tag.py <filepath> [--quiet] [--force] [--tags tag1,tag2,...]
"""

import sys
import json
import os
from pathlib import Path
from typing import List
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

# Load embedding_utils module
embedding_utils = load_module("embedding_utils", SCRIPT_DIR / "embedding-utils.py")
LocalEmbeddingManager = embedding_utils.LocalEmbeddingManager

# Load apply_tags module
apply_tags = load_module("apply_tags", SCRIPT_DIR / "apply-tags.py")
apply_tags_to_file = apply_tags.apply_tags_to_file
update_redis_tracking = apply_tags.update_redis_tracking
generate_embeddings_for_tags = apply_tags.generate_embeddings_for_tags
read_file_with_frontmatter = apply_tags.read_file_with_frontmatter

# Load suggest_tags module
suggest_tags_module = load_module("suggest_tags", SCRIPT_DIR / "suggest_tags.py")
suggest_tags_with_sdk = suggest_tags_module.suggest_tags_with_sdk

# Internal configuration
SIMILARITY_THRESHOLD = 0.7
TAGGING_AGENT_VERSION = "1.2"  # Bump when making meaningful changes to tagging logic


def get_claude_suggestions(filepath: str, verbose: bool = True) -> List[str]:
    """
    Get tag suggestions using Anthropic SDK.

    Args:
        filepath: Path to the markdown file
        verbose: Whether to print progress

    Returns:
        List of suggested tags from Claude
    """
    if verbose:
        print(f"Getting tag suggestions from Claude for: {filepath}")

    try:
        # Call suggest_tags_with_sdk directly
        tags = suggest_tags_with_sdk(filepath, verbose=verbose)

        if not tags:
            if verbose:
                print("No tags suggested")
            return []

        if verbose:
            print(f"Claude suggested: {tags}")

        return tags

    except Exception as e:
        print(f"Error getting Claude suggestions: {e}", file=sys.stderr)
        return []


def refine_tags_with_similarity(suggested_tags: List[str], verbose: bool = True) -> List[str]:
    """
    Check each suggested tag for similarity against existing tags in Redis.
    Replace with existing similar tags when found (threshold: 0.7).

    Args:
        suggested_tags: Initial tag suggestions
        verbose: Whether to print progress

    Returns:
        Refined list of tags with duplicates removed
    """
    if not suggested_tags:
        return []

    if verbose:
        print("\nChecking for similar existing tags...")

    try:
        manager = LocalEmbeddingManager()
        refined_tags = []

        for tag in suggested_tags:
            # Find similar tags above threshold
            similar = manager.find_similar_tags(
                tag,
                threshold=SIMILARITY_THRESHOLD,
                max_results=1
            )

            if similar:
                # Use the most similar existing tag
                similar_tag, similarity = similar[0]
                refined_tags.append(similar_tag)
                if verbose:
                    print(f"  {tag} → {similar_tag} (similarity: {similarity:.3f})")
            else:
                # Keep original tag
                refined_tags.append(tag)
                if verbose:
                    print(f"  {tag} ✓ (keeping original)")

        # Remove duplicates while preserving order
        final_tags = []
        for tag in refined_tags:
            if tag not in final_tags:
                final_tags.append(tag)

        return final_tags

    except Exception as e:
        print(f"Error refining tags: {e}", file=sys.stderr)
        # Fall back to original suggestions
        return list(dict.fromkeys(suggested_tags))


def auto_tag_file(filepath: str, verbose: bool = True, force: bool = False, input_tags: List[str] = None) -> bool:
    """
    Complete auto-tagging pipeline for a single file.

    Args:
        filepath: Path to the markdown file
        verbose: Whether to print progress
        force: Force retagging even if already tagged with current version
        input_tags: Optional list of tags to use instead of getting Claude suggestions

    Returns:
        True if successful, False otherwise
    """
    try:
        # Validate file exists
        if not Path(filepath).exists():
            print(f"Error: File not found: {filepath}", file=sys.stderr)
            return False

        # Check if already tagged with current version
        if not force:
            frontmatter, _ = read_file_with_frontmatter(filepath)
            existing_version = frontmatter.get('tagging-agent-version')
            if existing_version == TAGGING_AGENT_VERSION:
                if verbose:
                    print(f"Skipping {filepath}: already tagged with version {TAGGING_AGENT_VERSION}")
                return True

        if verbose:
            print(f"\n{'='*60}")
            print(f"Auto-tagging: {filepath}")
            print('='*60)

        # Step 1: Get tags (either from input or Claude suggestions)
        if input_tags:
            if verbose:
                print(f"Using provided tags: {input_tags}")
            suggested_tags = input_tags
        else:
            suggested_tags = get_claude_suggestions(filepath, verbose)
            if not suggested_tags:
                print("No tags suggested - skipping file", file=sys.stderr)
                return False

        # Step 2: Refine with similarity checking
        refined_tags = refine_tags_with_similarity(suggested_tags, verbose)

        if verbose:
            print(f"\nFinal tags: {refined_tags}")

        # Step 3: Get old tags for Redis tracking
        frontmatter, _ = read_file_with_frontmatter(filepath)
        old_tags = frontmatter.get('tags', [])
        note_id = frontmatter.get('id')

        # Step 4: Apply tags to file
        if not apply_tags_to_file(filepath, refined_tags, note_id, tagging_version=TAGGING_AGENT_VERSION):
            print("Failed to apply tags to file", file=sys.stderr)
            return False

        # Step 5: Generate embeddings for new tags
        if verbose:
            print("\nGenerating embeddings for new tags...")
        generate_embeddings_for_tags(refined_tags)

        # Step 6: Update Redis tracking
        # Re-read to get note_id that might have been added
        frontmatter, _ = read_file_with_frontmatter(filepath)
        note_id = frontmatter.get('id')

        if note_id:
            if verbose:
                print("Updating Redis tracking...")
            update_redis_tracking(note_id, refined_tags, old_tags)
        else:
            print("Warning: No note ID found, skipping Redis updates", file=sys.stderr)

        if verbose:
            print(f"\n✅ Successfully tagged with {len(refined_tags)} tags")

        return True

    except Exception as e:
        print(f"Error in auto-tagging pipeline: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


def parse_tags_input(tags_str: str) -> List[str]:
    """
    Parse comma-separated tags string into list.

    Args:
        tags_str: Comma-separated string of tags

    Returns:
        List of cleaned tag strings

    Raises:
        ValueError: If input format is invalid
    """
    if not tags_str or not isinstance(tags_str, str):
        raise ValueError("Tags input must be a non-empty string")

    # Split by comma and clean whitespace
    tags = [tag.strip() for tag in tags_str.split(',')]

    # Filter out empty strings
    tags = [tag for tag in tags if tag]

    if not tags:
        raise ValueError("No valid tags found in input")

    return tags


def main():
    """CLI interface for tagging."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  tag.py <filepath>                      # Tag a single file")
        print("  tag.py <filepath> --quiet              # Minimal output")
        print("  tag.py <filepath> --force              # Force retag even if current version")
        print("  tag.py <filepath> --tags tag1,tag2,... # Use provided tags instead of Claude suggestions")
        print("")
        print("Examples:")
        print("  tag.py inbox/note.md")
        print("  tag.py inbox/note.md --quiet")
        print("  tag.py inbox/note.md --force")
        print("  tag.py inbox/note.md --tags productivity,note-taking,workflows")
        sys.exit(1)

    filepath = sys.argv[1]
    quiet_mode = "--quiet" in sys.argv
    force_mode = "--force" in sys.argv
    verbose = not quiet_mode

    # Parse --tags argument if provided
    input_tags = None
    for i, arg in enumerate(sys.argv):
        if arg == "--tags" and i + 1 < len(sys.argv):
            try:
                input_tags = parse_tags_input(sys.argv[i + 1])
            except ValueError as e:
                print(f"Error parsing tags: {e}", file=sys.stderr)
                sys.exit(1)
            break

    # Run the auto-tagging pipeline
    success = auto_tag_file(filepath, verbose=verbose, force=force_mode, input_tags=input_tags)

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
