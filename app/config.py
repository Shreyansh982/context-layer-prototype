from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Loads configuration values from the .env file.

    Every module imports this class instead of reading
    environment variables directly.
    """

    # Answer-generation providers. Gemini is the demo default; Groq remains
    # available as the automatic fallback.
    PRIMARY_LLM: str = "gemini"
    GEMINI_API_KEY: str
    GROQ_API_KEY: str
    GEMINI_MODEL: str = "gemini-3.6-flash"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Neo4j
    NEO4J_URI: str
    NEO4J_USERNAME: str
    NEO4J_PASSWORD: str

    # ChromaDB
    CHROMA_HOST: str
    CHROMA_PORT: int

    # Redis: shared short-term conversation state and durable work queues.
    REDIS_URL: str = "redis://localhost:6379/0"
    CONVERSATION_TTL_SECONDS: int = 86_400

    # Demo identity.  A real deployment replaces the dependency that uses
    # these settings with an OIDC/JWT principal resolver.
    DEMO_API_KEY: str = "change-me-before-demo"
    DEMO_USER_ID: str = "E0001"

    # Bounded request admission keeps a laptop responsive under burst load.
    MAX_CONCURRENT_REQUESTS: int = 8
    REQUEST_QUEUE_TIMEOUT_SECONDS: float = 2.0
    EXTERNAL_RETRY_ATTEMPTS: int = 2
    EXTERNAL_RETRY_BASE_SECONDS: float = 0.25
    LLM_TIMEOUT_SECONDS: float = 45.0
    MAX_PROMPT_CHARACTERS: int = 24_000
    MAX_QUERY_CHARACTERS: int = 8_000
    WORKER_MAX_ATTEMPTS: int = 4
    # Development default: direct Excel lookup remains available, but the
    # complete workbook is not materialized into the graph unless explicitly
    # requested.
    ENABLE_ENTERPRISE_SYNC: bool = False
    # Keep a fresh development graph free of bootstrap data.  Policy ingestion
    # remains available through scripts/ingest_company_facts.py.
    ENABLE_COMPANY_POLICY_SEED: bool = False
    ENTERPRISE_SYNC_INTERVAL_SECONDS: int = 86_400
    CANONICAL_FACTS_ENABLED: bool = True

    # Version 2 profile learning.  Signals live in Redis until a configurable
    # threshold is reached; only stable profile knowledge is written to Neo4j.
    PROFILE_LEARNING_ENABLED: bool = True
    PROFILE_MIN_DISTINCT_SESSIONS: int = 3
    PROFILE_SIGNAL_WINDOW_DAYS: int = 90
    PROFILE_SIGNAL_RETENTION_BUFFER_DAYS: int = 7
    PROFILE_POLICY_VERSION: str = "v2"
    PROFILE_JOB_MAX_ATTEMPTS: int = 4
    # Passive, Redis-only shadow evidence used by a future promotion policy.
    # It never controls current memory/profile persistence decisions.
    EVIDENCE_RETENTION_DAYS: int = 97
    # Passive calibration capture.  This never alters a writer's result.
    PROMOTION_COMPARISON_ENABLED: bool = True
    PROMOTION_COMPARISON_DIRECTORY: str = "logs"
    PROMOTION_COMPARISON_RETENTION_DAYS: int = 30
    PROMOTION_COMPARISON_COHORT: str = "live"
    PROCESS_ROLE: str = "unknown"
    ENTERPRISE_ON_DEMAND_CACHE_ENABLED: bool = True
    ENTERPRISE_CACHE_REFRESH_INTERVAL_SECONDS: int = 86_400
    ENTERPRISE_CACHE_MAX_STALE_SECONDS: int = 172_800

    # Local terminal/file performance diagnostics. No external telemetry stack
    # is required for the demo bottleneck investigation.
    PERFORMANCE_INSTRUMENTATION_ENABLED: bool = True
    PERFORMANCE_SUMMARY_INTERVAL_SECONDS: int = 300
    PERFORMANCE_SUMMARY_DIRECTORY: str = "logs"
    PERFORMANCE_SAMPLE_LIMIT: int = 2_000
    PERFORMANCE_REPORT_RETENTION_DAYS: int = 14

    # Routing diagnostics. Content is omitted by default because prompts and
    # enterprise records can contain private employee data.
    DEBUG_ROUTING_CONTENT: bool = False

    # Developer-only memory-pipeline diagnostics. Disabled mode performs no
    # trace I/O or validation queries.
    EXTRACTION_TRACE: bool = False
    EXTRACTION_TRACE_DIRECTORY: str = "logs/extraction"

    # Context Layer
    BUFFER_SIZE: int = 8

    TOP_K: int = 10

    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

# Create a single global settings object.
# The .env file is read only once when the application starts.

settings = Settings()
