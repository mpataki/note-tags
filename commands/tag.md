---
id: tag
aliases: []
tags:
  - automation
  - tagging
  - metadata
  - data-processing
  - cli-automation
tagging-agent-version: 1
---

# /tag

**Description:** Suggest and apply tags to a markdown file using Claude AI and the tagging pipeline

**Usage:** `/tag <filepath>`

**What it does:**

1. **Read file content** and the tagging guidelines in this obsidian/notes repository at ./.claude/tagging-guidelines.md
2. **Generate tag suggestions** using Claude AI based on discovery-focused tagging principles
3. **Pass suggestions to tag.py** which:
   - Checks similarity against existing tags
   - Replaces with similar existing tags when found
   - Applies refined tags to file frontmatter
   - Generates embeddings for any new tags
   - Updates Redis tracking

**Example:**

```
/tag inbox/meeting-notes.md
```

**Implementation:**

This command should:
1. Read the target file content
2. Read `.claude/tagging-guidelines.md` for context
3. Use Claude AI to suggest 3-5 tags following the discovery-focused principles
4. Format tags as comma-separated string
5. Call: `python3 .claude/tag.py "<filepath>" --tags <suggested-tags>`

**Output:**

The command will show:
- Tag suggestions from Claude with reasoning
- Similarity checking results from tag.py
- Final tags applied
- Redis tracking updates
- Embedding generation status

**Notes:**

- Follows discovery-focused tagging principles (broad/specific/cross-cutting spectrum)
- Files must have a unique `id` in frontmatter (will be generated if missing)
- Redis must be running on localhost:16379
