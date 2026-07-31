"""
Embedding Service

Converts text into vector embeddings using
BAAI/bge-small-en-v1.5.

The model is loaded only once when the application starts.
"""

from sentence_transformers import SentenceTransformer
from app.config import settings
from app.performance import timed


class EmbeddingService:
    """
    Generates embeddings for semantic search.
    """
    def __init__(self):
        print("Loading embedding model...")
        with timed("embedding.model_load"):
            self.model = SentenceTransformer(
                settings.EMBEDDING_MODEL
            )
        print("Embedding model loaded.")
        self._has_embedded = False

    def embed(self, text: str) -> list[float]:
        """
        Generate an embedding for a single text.
        """
        stage = "embedding.first" if not self._has_embedded else "embedding.subsequent"
        with timed(stage):
            embedding = self.model.encode(
                text,
                normalize_embeddings=True
            )

        self._has_embedded = True
        return embedding.tolist()

    def embed_batch(
        self,
        texts: list[str]
    ) -> list[list[float]]:
        """
        Generate embeddings for multiple texts.
        """
        stage = "embedding.first_batch" if not self._has_embedded else "embedding.subsequent_batch"
        with timed(stage):
            embeddings = self.model.encode(
                texts,
                normalize_embeddings=True
            )

        self._has_embedded = True
        return embeddings.tolist()

embedding_service = EmbeddingService()
