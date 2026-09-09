"""集中读取环境变量。根目录的 .env 会被自动加载。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 中间件 ----
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "ops"
    postgres_password: str = "ops_dev_pw"
    postgres_db: str = "ops_assistant"

    redis_host: str = "localhost"
    redis_port: int = 6379

    kafka_bootstrap_servers: str = "localhost:9092"
    doc_upload_topic: str = "document-upload-events"

    # ---- LLM / embedding(Day 10 起用到)----
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    chat_model: str = "gpt-4o-mini"

    llm_service_port: int = 8000

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
