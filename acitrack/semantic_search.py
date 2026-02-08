"""Semantic search module for publication embeddings.

This module provides:
- Embedding text generation from publications
- OpenAI embedding API integration
- Cosine similarity search
- Publication search functionality

Uses OpenAI's text-embedding-3-small model by default (1536 dimensions).
"""

import hashlib
import logging
import os
import struct
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Default embedding model
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIM = 1536

# Rate limiting
DEFAULT_MAX_PER_MINUTE = 200
MIN_DELAY_SECONDS = 60.0 / DEFAULT_MAX_PER_MINUTE


def get_openai_api_key() -> Optional[str]:
    """Get OpenAI API key from environment.

    Returns:
        API key string or None if not configured
    """
    return os.getenv("SPOTITEARLY_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")


def build_embedding_text(publication: Dict) -> str:
    """Build the text to embed for a publication.

    Combines title, abstract/summary, journal/source, and publication date
    into a single text for embedding.

    Args:
        publication: Dictionary with publication data

    Returns:
        Concatenated text for embedding
    """
    parts = []

    # Title (required)
    title = publication.get("title", "").strip()
    if title:
        parts.append(title)

    # Abstract or summary
    abstract = publication.get("raw_text", "").strip()
    summary = publication.get("summary", "").strip()
    if abstract:
        parts.append(abstract)
    elif summary:
        parts.append(summary)

    # Journal/source
    venue = publication.get("venue", "").strip()
    source = publication.get("source", "").strip()
    if venue:
        parts.append(f"Journal: {venue}")
    elif source:
        parts.append(f"Source: {source}")

    # Publication date
    pub_date = publication.get("published_date", "").strip()
    if pub_date:
        parts.append(f"Published: {pub_date}")

    return "\n\n".join(parts)


def compute_content_hash(text: str) -> str:
    """Compute SHA256 hash of the embedding input text.

    Args:
        text: Input text

    Returns:
        Hex-encoded SHA256 hash
    """
    normalized = text.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def embedding_to_bytes(embedding: np.ndarray) -> bytes:
    """Convert numpy embedding array to bytes for storage.

    Args:
        embedding: Numpy array of floats

    Returns:
        Bytes representation
    """
    return embedding.astype(np.float32).tobytes()


def bytes_to_embedding(data: bytes, dim: int) -> np.ndarray:
    """Convert bytes back to numpy embedding array.

    Args:
        data: Bytes representation
        dim: Expected dimension

    Returns:
        Numpy array of floats
    """
    return np.frombuffer(data, dtype=np.float32).reshape(dim)


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Cosine similarity score (-1 to 1)
    """
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return float(np.dot(vec1, vec2) / (norm1 * norm2))


def embed_text(
    text: str,
    model: str = DEFAULT_EMBEDDING_MODEL,
    api_key: str = None,
) -> Optional[np.ndarray]:
    """Embed a single text using OpenAI's embedding API.

    Args:
        text: Text to embed
        model: Embedding model name
        api_key: OpenAI API key (uses env var if not provided)

    Returns:
        Numpy array of embedding or None if failed
    """
    api_key = api_key or get_openai_api_key()
    if not api_key:
        logger.warning("OpenAI API key not configured, cannot generate embeddings")
        return None

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)

        response = client.embeddings.create(
            input=text,
            model=model,
        )

        embedding = response.data[0].embedding
        return np.array(embedding, dtype=np.float32)

    except ImportError:
        logger.error("openai package not installed")
        return None
    except Exception as e:
        logger.error("Failed to generate embedding: %s", e)
        return None


def embed_texts(
    texts: List[str],
    model: str = DEFAULT_EMBEDDING_MODEL,
    api_key: str = None,
    max_per_minute: int = DEFAULT_MAX_PER_MINUTE,
) -> List[Optional[np.ndarray]]:
    """Embed multiple texts using OpenAI's embedding API.

    Uses batching and rate limiting for efficiency.

    Args:
        texts: List of texts to embed
        model: Embedding model name
        api_key: OpenAI API key (uses env var if not provided)
        max_per_minute: Maximum requests per minute for rate limiting

    Returns:
        List of numpy arrays (None for failed embeddings)
    """
    api_key = api_key or get_openai_api_key()
    if not api_key:
        logger.warning("OpenAI API key not configured, cannot generate embeddings")
        return [None] * len(texts)

    if not texts:
        return []

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)

        results = []
        min_delay = 60.0 / max_per_minute

        # Process in batches (OpenAI supports up to 2048 texts per request)
        batch_size = min(100, len(texts))  # Conservative batch size

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]

            try:
                response = client.embeddings.create(
                    input=batch,
                    model=model,
                )

                # Extract embeddings in order
                batch_embeddings = [None] * len(batch)
                for item in response.data:
                    idx = item.index
                    batch_embeddings[idx] = np.array(item.embedding, dtype=np.float32)

                results.extend(batch_embeddings)

            except Exception as e:
                logger.error("Failed to generate embeddings for batch %d: %s", i // batch_size, e)
                results.extend([None] * len(batch))

            # Rate limiting
            if i + batch_size < len(texts):
                time.sleep(min_delay)

        return results

    except ImportError:
        logger.error("openai package not installed")
        return [None] * len(texts)
    except Exception as e:
        logger.error("Failed to generate embeddings: %s", e)
        return [None] * len(texts)


def search_publications(
    query: str,
    top_k: int = 10,
    since_days: Optional[int] = None,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    db_path: str = None,
    database_url: str = None,
) -> List[Dict]:
    """Search publications using semantic similarity.

    Args:
        query: Query text
        top_k: Number of results to return
        since_days: Only search publications from the last N days
        embedding_model: Name of the embedding model
        db_path: Path to SQLite database (for SQLite backend)
        database_url: PostgreSQL connection URL (for PG backend)

    Returns:
        List of publication dicts with similarity scores, sorted by score descending
    """
    from storage.store import get_store, get_database_url as get_db_url

    # Get query embedding
    query_embedding = embed_text(query, model=embedding_model)
    if query_embedding is None:
        logger.warning("Failed to generate query embedding")
        return []

    # Get store and database URL
    store = get_store()
    database_url = database_url or get_db_url()

    # Get all embeddings for the model
    if database_url:
        embeddings_data = store.get_all_embeddings_for_model(
            embedding_model=embedding_model,
            since_days=since_days,
            database_url=database_url,
        )
    else:
        embeddings_data = store.get_all_embeddings_for_model(
            embedding_model=embedding_model,
            since_days=since_days,
            db_path=db_path or "data/db/acitrack.db",
        )

    if not embeddings_data:
        logger.info("No embeddings found for model %s", embedding_model)
        return []

    # Compute similarities
    results = []
    for item in embeddings_data:
        try:
            doc_embedding = bytes_to_embedding(item["embedding"], item["embedding_dim"])
            similarity = cosine_similarity(query_embedding, doc_embedding)

            results.append({
                "publication_id": item["publication_id"],
                "title": item["title"],
                "source": item["source"],
                "published_date": item["published_date"],
                "canonical_url": item["canonical_url"],
                "similarity": similarity,
            })
        except Exception as e:
            logger.warning("Failed to compute similarity for %s: %s", item["publication_id"][:16], e)

    # Sort by similarity descending
    results.sort(key=lambda x: x["similarity"], reverse=True)

    # Return top_k results
    return results[:top_k]


def get_embedding_dimension(model: str = DEFAULT_EMBEDDING_MODEL) -> int:
    """Get the embedding dimension for a model.

    Args:
        model: Embedding model name

    Returns:
        Embedding dimension
    """
    # Known model dimensions
    dimensions = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }
    return dimensions.get(model, DEFAULT_EMBEDDING_DIM)


class SemanticSearchIndex:
    """In-memory semantic search index for faster searching.

    Loads embeddings from the database into memory for fast similarity search.
    Useful when performing many queries against the same dataset.
    """

    def __init__(
        self,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        since_days: Optional[int] = None,
        db_path: str = None,
        database_url: str = None,
    ):
        """Initialize the search index.

        Args:
            embedding_model: Name of the embedding model
            since_days: Only load embeddings from the last N days
            db_path: Path to SQLite database
            database_url: PostgreSQL connection URL
        """
        self.embedding_model = embedding_model
        self.embeddings: List[np.ndarray] = []
        self.metadata: List[Dict] = []
        self._loaded = False

        # Load embeddings
        self._load_embeddings(since_days, db_path, database_url)

    def _load_embeddings(
        self,
        since_days: Optional[int],
        db_path: str,
        database_url: str,
    ):
        """Load embeddings from the database."""
        from storage.store import get_store, get_database_url as get_db_url

        store = get_store()
        database_url = database_url or get_db_url()

        # Get all embeddings for the model
        if database_url:
            embeddings_data = store.get_all_embeddings_for_model(
                embedding_model=self.embedding_model,
                since_days=since_days,
                database_url=database_url,
            )
        else:
            embeddings_data = store.get_all_embeddings_for_model(
                embedding_model=self.embedding_model,
                since_days=since_days,
                db_path=db_path or "data/db/acitrack.db",
            )

        for item in embeddings_data:
            try:
                embedding = bytes_to_embedding(item["embedding"], item["embedding_dim"])
                self.embeddings.append(embedding)
                self.metadata.append({
                    "publication_id": item["publication_id"],
                    "title": item["title"],
                    "source": item["source"],
                    "published_date": item["published_date"],
                    "canonical_url": item["canonical_url"],
                })
            except Exception as e:
                logger.warning("Failed to load embedding for %s: %s", item["publication_id"][:16], e)

        self._loaded = True
        logger.info("Loaded %d embeddings into search index", len(self.embeddings))

    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """Search the index using semantic similarity.

        Args:
            query: Query text
            top_k: Number of results to return

        Returns:
            List of publication dicts with similarity scores
        """
        if not self._loaded or not self.embeddings:
            return []

        # Get query embedding
        query_embedding = embed_text(query, model=self.embedding_model)
        if query_embedding is None:
            return []

        # Compute similarities
        results = []
        for i, doc_embedding in enumerate(self.embeddings):
            similarity = cosine_similarity(query_embedding, doc_embedding)
            results.append({
                **self.metadata[i],
                "similarity": similarity,
            })

        # Sort by similarity descending
        results.sort(key=lambda x: x["similarity"], reverse=True)

        return results[:top_k]

    def __len__(self) -> int:
        """Return the number of embeddings in the index."""
        return len(self.embeddings)
