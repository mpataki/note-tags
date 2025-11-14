# Note Tagging System

Automated tagging for Obsidian notes using Claude AI and Redis Stack vector search. Suggests tags, prevents duplicates using semantic similarity, and maintains consistent vocabulary across your vault.

## What It Does

1. Claude AI suggests 3-5 tags based on note content
2. Checks each tag against existing tags using vector embeddings
3. Replaces with similar existing tags (0.7+ similarity threshold)
4. Applies tags to note frontmatter
5. Tracks usage in Redis

## Requirements

- Python 3.8+
- Redis Stack (needs RediSearch module for vector search)
- Anthropic API key

## Installation

```bash
# macOS - install Redis Stack
brew install redis-stack

# Run in foreground
redis-stack-server --port 16379

# Or as background service (recommended)
cp com.redis.redis-stack.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.redis.redis-stack.plist
launchctl start com.redis.redis-stack

# Docker option
docker run -d -p 16379:6379 redis/redis-stack:latest

# Install Python packages
pip install anthropic sentence-transformers redis numpy

# Set API key
export ANTHROPIC_API_KEY="your-key"

# Create Redis vector index (one-time)
python3 setup-vector-index.py
```

## Usage

```bash
# Tag a file
python3 tag.py path/to/note.md

# Use manual tags instead of AI
python3 tag.py path/to/note.md --tags productivity,automation,workflows

# Force retag already-tagged file
python3 tag.py path/to/note.md --force

# Rebuild Redis from existing files
python3 reseed-tags.py /path/to/vault --flush

# Find similar tags
python3 embedding-utils.py similar productivity

# Find potential duplicates
python3 embedding-utils.py merges
```

## How It Works

**Embeddings**: Uses Hugging Face `all-MiniLM-L6-v2` (384 dimensions) to generate tag embeddings

**Vector search**: Redis Stack HNSW index with COSINE distance for fast similarity lookup

**Tagging guidelines**: See `tagging-guidelines.md` - focuses on discovery across broad/specific/cross-cutting dimensions

## Components

**tag.py** - Main orchestrator. Coordinates AI suggestions, similarity checking, file updates, and Redis tracking.

**suggest_tags.py** - Calls Claude API to suggest tags based on note content and guidelines.

**embedding-utils.py** - Generates embeddings, stores in Redis, provides similarity search. CLI for finding similar tags and merge candidates.

**apply-tags.py** - Reads/writes YAML frontmatter, updates Redis tag usage and note relationships.

**setup-vector-index.py** - One-time setup for Redis HNSW vector index.

**reseed-tags.py** - Rebuilds Redis data from existing markdown files.

## Configuration

### macOS LaunchAgent

`com.redis.redis-stack.plist` runs Redis Stack as a background service:
- Auto-starts on boot (`RunAtLoad`)
- Auto-restarts on crash (`KeepAlive`)
- Logs to `/opt/homebrew/var/log/redis-stack.log`

```bash
# Check status
launchctl list | grep redis-stack

# View logs
tail -f /opt/homebrew/var/log/redis-stack.log

# Restart
launchctl stop com.redis.redis-stack && launchctl start com.redis.redis-stack
```

### Redis Connection

Default: `localhost:16379`

Change in scripts if needed:
```python
r = redis.Redis(host='localhost', port=16379, decode_responses=False)
```

### Similarity Thresholds

- **tag.py**: `SIMILARITY_THRESHOLD = 0.7` for tag consolidation
- **embedding-utils.py**: Adjustable thresholds for similarity search and merge detection

### Tagging Version

Bump `TAGGING_AGENT_VERSION` in `tag.py` when changing logic. Prevents retagging files with same version (override with `--force`).

## Data Storage

**Redis keys:**
- `tag_embeddings:<tag>` - Binary vector (float32, 384 dims), model name, dimensions
- `tag_usage:<tag>` - Usage count, array of note IDs

**Note frontmatter:**
```yaml
---
id: unique-note-id
tags:
  - productivity
  - automation
tagging-agent-version: 1.2
---
```

## Troubleshooting

**Cannot connect to Redis**
```bash
redis-cli -p 16379 ping
redis-cli -p 16379 MODULE LIST  # Verify RediSearch
launchctl list | grep redis-stack  # Check service
```

**Index not found**
```bash
python3 setup-vector-index.py
```

**Missing embeddings**
```bash
python3 embedding-utils.py generate-all
```

**Stale data**
```bash
python3 reseed-tags.py /path/to/vault --flush
```

## Claude Code Integration

`/tag` slash command available for use within Claude Code:
```
/tag inbox/meeting-notes.md
```

See `commands/tag.md` for details.

## License

MIT
