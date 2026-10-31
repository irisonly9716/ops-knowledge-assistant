"""All environment variables in one place. The repository-root .env is loaded automatically."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- infrastructure ----
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "ops"
    postgres_password: str = ""  # no default password in code (the repo is public); it must come from .env
    postgres_db: str = "ops_assistant"

    redis_host: str = "localhost"
    redis_port: int = 6379

    kafka_bootstrap_servers: str = "localhost:9092"
    doc_upload_topic: str = "document-upload-events"

    # ---- document ingestion consumer ----
    # Root folder of uploaded files; the same .env variable backend-service reads. A relative
    # path resolves against THIS service's working directory (llm-service/), so ../uploads
    # is the uploads/ folder at the repository root.
    upload_dir: str = "../uploads"
    # Consumer group. Instances in one group share the partitions; a new name is a brand-new
    # group that starts reading from wherever auto.offset.reset points.
    doc_consumer_group: str = "llm-service-document-ingest"
    # How many times to try a failing message (including the first try) before it goes to
    # the dead-letter topic
    doc_consumer_max_attempts: int = 3
    # Wait before a retry: after the n-th failure wait backoff * 2^(n-1) seconds (exponential backoff)
    doc_consumer_backoff_seconds: float = 0.5

    # ---- LLM / embeddings ----
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    # The SDK retries connection errors, 408/409/429 and 5xx by itself, with exponential backoff and
    # honoring Retry-After; 5 retries ride out a short rate-limit burst. The consumer's own retries
    # (doc_consumer_max_attempts) come on top of these
    openai_max_retries: int = 5
    embedding_model: str = "text-embedding-3-small"
    # The cheapest tier by default. Switching a task to a stronger model (SQL generation,
    # campaign suggestions, eval grading) is decided by eval accuracy, latency and cost,
    # not by version number
    chat_model: str = "gpt-6-luna"

    llm_service_port: int = 8000

    @property
    def doc_upload_dlq_topic(self) -> str:
        """Dead-letter topic name, derived from the main topic ("<topic>.dlq" is the common
        convention) instead of a separate variable, so renaming the main topic renames
        the dead-letter topic too."""
        return f"{self.doc_upload_topic}.dlq"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
