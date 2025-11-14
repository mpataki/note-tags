#!/usr/bin/env python3
"""
Setup Redis vector search index for tag embeddings.
This is a one-time setup that creates the HNSW index for fast similarity search.

Usage:
    python3 .claude/setup-vector-index.py              # Create index
    python3 .claude/setup-vector-index.py --recreate   # Drop and recreate index
"""

import sys
import redis

# Redis connection
r = redis.Redis(host='localhost', port=16379, decode_responses=False)

# Index configuration
# NOTE: VECTOR_DIM must match the Hugging Face model used in embedding-utils.py
# Current model: all-MiniLM-L6-v2 produces 384-dimensional vectors
INDEX_NAME = "tag_idx"
PREFIX = "tag_embeddings:"
VECTOR_DIM = 384  # Must match the embedding model dimensions
DISTANCE_METRIC = "COSINE"


def index_exists() -> bool:
    """Check if the vector index already exists."""
    try:
        r.ft(INDEX_NAME).info()
        return True
    except:
        return False


def drop_index():
    """Drop the existing vector index."""
    try:
        r.ft(INDEX_NAME).dropindex()
        print(f"✅ Dropped existing index '{INDEX_NAME}'")
        return True
    except Exception as e:
        print(f"⚠️  Could not drop index: {e}")
        return False


def create_index():
    """Create the vector search index."""
    try:
        from redis.commands.search.field import VectorField, TagField, NumericField
        from redis.commands.search.indexDefinition import IndexDefinition, IndexType

        # Define schema
        schema = (
            VectorField(
                "embedding",
                "HNSW",
                {
                    "TYPE": "FLOAT32",
                    "DIM": VECTOR_DIM,
                    "DISTANCE_METRIC": DISTANCE_METRIC
                }
            ),
            TagField("model"),
            NumericField("dimensions")
        )

        # Create index
        r.ft(INDEX_NAME).create_index(
            schema,
            definition=IndexDefinition(
                prefix=[PREFIX],
                index_type=IndexType.HASH
            )
        )

        print(f"✅ Created vector search index '{INDEX_NAME}'")
        print(f"   - Prefix: {PREFIX}")
        print(f"   - Dimensions: {VECTOR_DIM}")
        print(f"   - Distance metric: {DISTANCE_METRIC}")
        print(f"   - Algorithm: HNSW")
        return True

    except Exception as e:
        print(f"❌ Error creating index: {e}")
        return False


def show_index_info():
    """Display information about the index."""
    try:
        info = r.ft(INDEX_NAME).info()

        # Extract key info (info comes back as a list of key-value pairs)
        info_dict = {}
        for i in range(0, len(info), 2):
            key = info[i].decode('utf-8') if isinstance(info[i], bytes) else info[i]
            value = info[i+1]
            if isinstance(value, bytes):
                value = value.decode('utf-8')
            info_dict[key] = value

        print("\n📊 Index Information:")
        print(f"   - Index name: {info_dict.get('index_name', 'N/A')}")
        print(f"   - Documents indexed: {info_dict.get('num_docs', 'N/A')}")
        print(f"   - Index status: {info_dict.get('indexing', 'N/A')}")

    except Exception as e:
        print(f"⚠️  Could not retrieve index info: {e}")


def main():
    """Main setup routine."""
    recreate = "--recreate" in sys.argv

    print("="*60)
    print("Redis Vector Search Index Setup")
    print("="*60)

    # Check Redis connection
    try:
        r.ping()
        print("✅ Connected to Redis")
    except Exception as e:
        print(f"❌ Cannot connect to Redis: {e}")
        print("   Make sure Redis Stack is running on localhost:16379")
        sys.exit(1)

    # Check for RediSearch module
    try:
        modules = r.module_list()
        module_names = [m[b'name'].decode('utf-8') if isinstance(m[b'name'], bytes) else m[b'name'] for m in modules]

        if 'search' not in module_names and 'searchlight' not in module_names:
            print("❌ RediSearch module not found")
            print("")
            print("This system requires Redis Stack (not vanilla Redis).")
            print("Redis Stack includes the RediSearch module needed for vector search.")
            print("")
            print("Installation options:")
            print("  macOS:  brew install redis-stack")
            print("  Docker: docker run -d -p 16379:6379 redis/redis-stack:latest")
            print("  Other:  https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/")
            print("")
            print(f"Current modules: {', '.join(module_names) if module_names else 'none'}")
            sys.exit(1)

        print("✅ RediSearch module detected")
    except Exception as e:
        print(f"⚠️  Could not check for RediSearch module: {e}")
        print("   Continuing anyway...")

    # Check if index exists
    exists = index_exists()

    if exists:
        print(f"ℹ️  Index '{INDEX_NAME}' already exists")

        if recreate:
            print("🔄 Recreating index...")
            drop_index()
            if not create_index():
                sys.exit(1)
        else:
            print("   Use --recreate to drop and recreate the index")
            show_index_info()
            sys.exit(0)
    else:
        print(f"ℹ️  Index '{INDEX_NAME}' does not exist")
        print("🔨 Creating index...")
        if not create_index():
            sys.exit(1)

    # Show index info
    show_index_info()

    print("\n✅ Setup complete!")
    print("\nNext steps:")
    print("1. Run: python3 .claude/reseed-tags.py . --flush")
    print("   This will generate embeddings for all existing tags")
    print("\n2. Test similarity search:")
    print("   python3 .claude/embedding-utils.py similar productivity")

    sys.exit(0)


if __name__ == "__main__":
    main()
