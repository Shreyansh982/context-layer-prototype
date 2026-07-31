"""
ChromaDB connection manager.

Creates and manages the memory collection.
"""
import chromadb
from app.config import settings
from app.resilience import retry_external
from app.performance import timed


class ResilientCollection:
    """Proxy that turns Chroma transport failures into retryable 503 errors."""
    def __init__(self, collection):
        self.collection = collection

    def __getattr__(self, name):
        attribute = getattr(self.collection, name)
        if not callable(attribute):
            return attribute
        def instrumented(*args, **kwargs):
            with timed(f"chroma.{name}"):
                return retry_external("ChromaDB", lambda: attribute(*args, **kwargs))
        return instrumented

class ChromaConnection:
    def __init__(self):
        self.client = chromadb.HttpClient(
            host=settings.CHROMA_HOST,
            port=settings.CHROMA_PORT
        )
        # Create the collection only once.
        self.collection = ResilientCollection(self.client.get_or_create_collection(
            name="memory"
        ))
        self.fact_collection = ResilientCollection(self.client.get_or_create_collection(
            name="canonical_fact"
        ))
    def get_collection(self):
        return self.collection

    def get_fact_collection(self):
        """Canonical fact projection; separate from legacy sentence memories."""
        return self.fact_collection

chroma = ChromaConnection()
