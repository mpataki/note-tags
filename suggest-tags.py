#!/usr/bin/env python3
"""
Tag suggestion using Anthropic SDK with structured outputs.
Uses tool calling to enforce JSON schema for reliable tag extraction.
"""

import os
import sys
from pathlib import Path
from typing import List
import anthropic

# Load tagging guidelines
SCRIPT_DIR = Path(__file__).parent
GUIDELINES_FILE = SCRIPT_DIR / "tagging-guidelines.md"

def load_tagging_guidelines() -> str:
    """Load tagging guidelines."""
    try:
        with open(GUIDELINES_FILE, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not load guidelines: {e}", file=sys.stderr)
        return "Suggest relevant tags for the content."

def read_file_content(filepath: str) -> str:
    """Read markdown file content."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"Error reading file {filepath}: {e}", file=sys.stderr)
        sys.exit(1)

def suggest_tags_with_sdk(filepath: str, verbose: bool = True) -> List[str]:
    """
    Use Anthropic SDK with tool calling to get structured tag suggestions.

    Args:
        filepath: Path to the markdown file
        verbose: Whether to print progress

    Returns:
        List of suggested tags
    """
    # Get API key from environment (try tagging-specific key first)
    api_key = os.environ.get("ANTHROPIC_API_KEY_FOR_TAGGING") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: Neither ANTHROPIC_API_KEY_FOR_TAGGING nor ANTHROPIC_API_KEY environment variable is set", file=sys.stderr)
        sys.exit(1)

    # Initialize client
    client = anthropic.Anthropic(api_key=api_key)

    # Load file content and guidelines
    file_content = read_file_content(filepath)
    guidelines = load_tagging_guidelines()

    # Define the tool for structured output
    tools = [
        {
            "name": "suggest_tags",
            "description": "Suggest tags for a note based on its content",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
                            "description": "Tag in lowercase-with-hyphens format"
                        },
                        "description": "Array of discovery-focused tags"
                    }
                },
                "required": ["tags"]
            }
        }
    ]

    # Create the prompt
    prompt = f"""You are a tagging expert helping organize notes in an Obsidian vault.

Here are the tagging guidelines:

{guidelines}

Now, analyze this file and suggest appropriate tags:

File: {filepath}

Content:
{file_content}

Use the suggest_tags tool to return your suggested tags following the guidelines above."""

    if verbose:
        print(f"Requesting tag suggestions from Claude API...", file=sys.stderr)

    try:
        # Make API request with tool use
        response = client.messages.create(
            model="claude-3-5-haiku-latest",  # Auto-updates to latest Haiku
            #model="claude-sonnet-4-5",  # Auto-updates to latest Sonnet 4.5
            max_tokens=1024,
            tools=tools,
            tool_choice={"type": "tool", "name": "suggest_tags"},  # Force tool use
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        # Extract tags from tool use
        for block in response.content:
            if block.type == "tool_use" and block.name == "suggest_tags":
                tags = block.input.get("tags", [])

                if verbose:
                    print(f"Received {len(tags)} tags from API", file=sys.stderr)

                return tags

        # If we get here, no tool use was found
        print("Error: No tool use found in response", file=sys.stderr)
        return []

    except anthropic.APIError as e:
        print(f"Anthropic API error: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return []

def main():
    """CLI interface for tag suggestion."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  suggest_tags.py <filepath>              # Suggest tags for file")
        print("  suggest_tags.py <filepath> --quiet      # Minimal output")
        print("")
        print("Requires: ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    filepath = sys.argv[1]
    quiet_mode = "--quiet" in sys.argv
    verbose = not quiet_mode

    # Validate file exists
    if not Path(filepath).exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    # Get tag suggestions
    tags = suggest_tags_with_sdk(filepath, verbose=verbose)

    if not tags:
        print("Error: No tags suggested", file=sys.stderr)
        sys.exit(1)

    # Output as JSON array (for programmatic use)
    import json
    print(json.dumps(tags))

    sys.exit(0)

if __name__ == "__main__":
    main()
