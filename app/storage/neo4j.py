"""
Neo4j connection manager.

This file is responsible only for creating and managing the
connection to Neo4j.

No Cypher queries should be written here.
"""

from neo4j import GraphDatabase

from app.config import settings
from app.resilience import retry_external
from app.performance import timed


class TimedResult:
    """Measure lazy Bolt result consumption separately from ``session.run``."""
    def __init__(self, result):
        self.result = result

    def __iter__(self):
        with timed("neo4j.result_fetch"):
            yield from self.result

    def single(self, *args, **kwargs):
        with timed("neo4j.result_fetch"):
            return self.result.single(*args, **kwargs)

    def consume(self, *args, **kwargs):
        with timed("neo4j.result_fetch"):
            return self.result.consume(*args, **kwargs)

    def data(self, *args, **kwargs):
        with timed("neo4j.result_fetch"):
            return self.result.data(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.result, name)


class ResilientSession:
    """Session proxy used by services so transient graph calls are bounded."""
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        self.session.__enter__()
        return self

    def __exit__(self, *args):
        return self.session.__exit__(*args)

    def run(self, *args, **kwargs):
        # This is the driver's RUN/first-response boundary. Connection-pool
        # acquisition and a cold Bolt handshake may occur here; lazy records
        # are measured separately by TimedResult.
        with timed("neo4j.session_run"):
            return TimedResult(retry_external("Neo4j", lambda: self.session.run(*args, **kwargs)))

    def execute_write(self, function, *args, **kwargs):
        """Run a transactional write while preserving the common retry policy."""
        with timed("neo4j.transaction"):
            return retry_external("Neo4j", lambda: self.session.execute_write(function, *args, **kwargs))


class Neo4jConnection:
    """
    Creates a single reusable Neo4j driver.

    Every service shares this driver instead of creating
    a new connection every time.
    """

    def __init__(self):

        self.driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(
                settings.NEO4J_USERNAME,
                settings.NEO4J_PASSWORD
            ),
            connection_timeout=settings.LLM_TIMEOUT_SECONDS,
            max_connection_pool_size=50,
        )

    def get_session(self):
        """
        Returns a new Neo4j session.

        A session is lightweight and should be opened
        only when required.
        """

        return ResilientSession(self.driver.session())

    def diagnose_connectivity(self) -> None:
        """Explicit diagnostic probe; not used on normal request paths."""
        with timed("neo4j.connectivity_handshake"):
            retry_external("Neo4j connectivity", self.driver.verify_connectivity)

    def close(self):
        """
        Close the Neo4j driver when the application shuts down.
        """

        self.driver.close()


neo4j = Neo4jConnection()
