from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # Database
    DATABASE_URL: str  # App role (rag_app) - subject to RLS
    DATABASE_ADMIN_URL: str  # Admin role (rag_admin) - for migrations

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "documents"
    MINIO_SECURE: bool = False

    # Clerk
    CLERK_SECRET_KEY: str = ""
    CLERK_PUBLISHABLE_KEY: str = ""
    CLERK_WEBHOOK_SECRET: str = ""
    CLERK_JWT_ISSUER: str = ""

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_METER_QUERIES_ID: str = ""
    STRIPE_METER_DOCUMENTS_ID: str = ""
    STRIPE_METER_STORAGE_ID: str = ""
    STRIPE_PRICE_STARTER: str = "price_starter_monthly"
    STRIPE_PRICE_PROFESSIONAL: str = "price_professional_monthly"
    STRIPE_PRICE_ENTERPRISE: str = "price_enterprise_monthly"
    STRIPE_TRIAL_DAYS: int = 14

    # LiteLLM
    LITELLM_PROXY_URL: str = "http://localhost:4000"
    LITELLM_MASTER_KEY: str = ""

    # Jina AI
    JINA_API_KEY: str = ""

    # Embeddings
    EMBEDDINGS_URL: str = "http://localhost:8082"
    EMBEDDINGS_MODEL: str = "bge-m3"
    EMBEDDINGS_PROVIDER: str = "self-hosted"  # "self-hosted" or "jina-v4"

    # Reranker
    RERANKER_URL: str = "http://localhost:8083"
    RERANKER_MODEL: str = "bge-reranker-v2-m3"
    RERANKER_PROVIDER: str = "self-hosted"  # "self-hosted" or "jina"

    # Query Decomposition
    ENABLE_QUERY_DECOMPOSITION: bool = True
    DECOMPOSITION_MODEL: str = "gpt-4o-mini"

    # Generation
    DEFAULT_LLM_MODEL: str = "gpt-4o"
    MAX_CONTEXT_TOKENS: int = 6000
    RESERVED_ANSWER_TOKENS: int = 1500
    QUERY_RATE_LIMIT_PER_MINUTE: int = 20
    MAX_CONVERSATION_HISTORY: int = 10

    # Connectors
    CONNECTOR_ENCRYPTION_KEY: str = ""  # Fernet key for credential encryption
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/connectors/oauth/callback"

    # Docling
    DOCLING_URL: str = "http://localhost:8081"

    # Langfuse
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = ""

    # Document webhooks
    DOCUMENT_WEBHOOK_URL: str = ""  # POST target for processing status events

    # Application
    APP_ENV: str = "development"
    APP_DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = ""

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"


settings = Settings()
