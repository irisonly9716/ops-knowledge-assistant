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
    postgres_password: str = ""  # 不在代码里写默认密码(仓库是公开的),必须从 .env 读
    postgres_db: str = "ops_assistant"

    redis_host: str = "localhost"
    redis_port: int = 6379

    kafka_bootstrap_servers: str = "localhost:9092"
    doc_upload_topic: str = "document-upload-events"

    # ---- 文档摄入消费者 ----
    # 上传文件的存放根目录,跟 backend-service 读的是同一个 .env 变量。相对路径按【本服务的
    # 工作目录】(llm-service/)解析,../uploads 恰好就是仓库根的 uploads/。
    upload_dir: str = "../uploads"
    # 消费组名。同一个组里的多个消费者实例会分摊分区;换一个名字 = 一个全新的组,
    # 会从 auto.offset.reset 指定的位置重新开始读。
    doc_consumer_group: str = "llm-service-document-ingest"
    # 处理一条消息失败后,最多尝试几次(含第一次),仍失败就进死信 topic
    doc_consumer_max_attempts: int = 3
    # 重试前的等待:第 n 次失败后等 backoff * 2^(n-1) 秒(指数退避)
    doc_consumer_backoff_seconds: float = 0.5

    # ---- LLM / embedding(向量化与问答用到)----
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    # 默认用最便宜的一档。按任务换更强的模型(SQL 生成、出策划建议、eval 评审)时,
    # 用 eval 的准确率、延迟、成本来决定,不按版本号选
    chat_model: str = "gpt-6-luna"

    llm_service_port: int = 8000

    @property
    def doc_upload_dlq_topic(self) -> str:
        """死信 topic 的名字,从主 topic 名派生("<主topic>.dlq"是业界通用命名),
        不另开一个环境变量——主 topic 改名时死信 topic 自动跟着变,不会漏改。"""
        return f"{self.doc_upload_topic}.dlq"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
