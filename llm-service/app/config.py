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
    # How many deliveries of one upload may start without finishing before it goes to the dead-letter topic. Not
    # the retries above: those are counted in memory and start over if the process dies
    doc_consumer_max_deliveries: int = 3
    # Wait before a retry: after the n-th failure wait backoff * 2^(n-1) seconds (exponential backoff)
    doc_consumer_backoff_seconds: float = 0.5
    # Longest a consumer may go between two polls; past it Kafka hands its partition to another member,
    # and the late commit fails. One message is handled between two polls, so its time must stay below this
    doc_consumer_max_poll_interval_ms: int = 300_000
    # All the time one message may take, retries included. Checked against the limit above at startup
    doc_consumer_message_budget_seconds: float = 200.0

    # ---- LLM / embeddings ----
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    # One embedding request; retries are inside the consumer's budget for the message
    embedding_request_timeout_seconds: float = 20.0
    # The cheapest tier by default. Switching a task to a stronger model (SQL generation,
    # campaign suggestions, eval grading) is decided by eval accuracy, latency and cost,
    # not by version number
    chat_model: str = "gpt-6-luna"

    # ---- query path ----
    # Shared with the backend. /query trusts the role and department in the request, so it only accepts calls
    # carrying this token. No default: without it the service refuses to start
    internal_service_token: str = ""
    # All the time one query may take. Well above the 3 s target because a single answer was measured at 8 s; the
    # backend's read timeout has to stay above it
    query_budget_seconds: float = 12.0
    query_embedding_timeout_seconds: float = 3.0
    query_model_timeout_seconds: float = 10.0
    answer_max_output_tokens: int = 800
    query_top_k: int = 5
    # Below this similarity a question is refused without asking the model. Chosen on the evaluation's dev questions
    # (docs/eval-report.md): no question to refuse scored below 0.33, so no threshold up to the lowest answerable
    # question (0.27) stops a single wrong answer; the model does that. What is left for the threshold is saving a
    # model call on questions far from everything, which scored 0.10 to 0.25 (8 unrelated probes). 0.14 is the middle
    # of the range of lowest cost, well clear of answerable questions: it stops the farthest of those probes and
    # leaves the rest to the model, because a refused real question costs more than a model call (about $0.0001)
    query_min_similarity: float = 0.14
    # Text Embeddings Inference serving bge-reranker-v2-m3 (docker-compose.yml, profile "rerank"). Empty: no reranking.
    # Not reachable or too slow, every query falls back to vector order with a warning (app/rag/reranking.py)
    reranker_url: str = "http://127.0.0.1:8082"
    # Candidates the search returns for the reranker; the prompt still gets query_top_k. On dev, 30 and 50 reranked to
    # the same Recall@5 (92.7%), but with 30 two labelled sections were never candidates, and the corpus will grow
    rerank_candidates: int = 50
    # The 3 s target minus the rest of a query (measured p50 1.4 s, p95 2.6 s, without the backend) leaves little. A
    # reranker on a GPU is designed to take a fraction of this, not measured yet (#11); the limit bounds what a slow
    # or overloaded one adds before the query goes on in vector order. On a development CPU it takes 44 s and always
    # falls back
    rerank_timeout_seconds: float = 0.5
    # Model calls at once in this process, embeddings and answers together. The peak is 900 in flight across 4 processes.
    # Not an exact share of it, because workers on one port don't get equal work: measured at 300 QPS, 225 each refused
    # 53 of 1,500 queries and 300 each refused none, with at most 898 at once. One process couldn't use many more anyway.
    # Development can set a low value in .env because those tests are paid for privately; that says nothing about production
    model_max_concurrent_calls: int = 300
    # How long a query waits for a free slot before it is refused. A queue that keeps growing helps nobody: at the
    # peak about 300 slots free up per second, so this holds about a second of work
    model_slot_wait_seconds: float = 1.0
    # Per process, and Postgres allows 100 in total, shared with the backend and the consumers. A connection is held only
    # for the search, so few are needed. Measured at 300 QPS on 4 processes: 5 each was as fast as 20 each, with at most 2
    # statements running at once. At 20 each, 8 processes took 99 connections and Postgres started refusing them
    query_pool_size: int = 5

    # ---- Text-to-SQL ----
    # The model that writes SQL, set apart from the one that answers and explains: which one is decided by the
    # evaluation's accuracy, latency and cost
    sql_model: str = "gpt-6-luna"
    # Example questions shown with a question (app/sql_agent/examples.yaml); 0 shows none
    sql_examples: int = 3
    # How many times a query is written again after it fails, with the error shown to the model. Each is a model call
    # inside the query's time budget
    sql_retries: int = 2
    # The password of sql_reader, the role generated SQL runs as (migration V11). The migration creates the role
    # without a login; scripts.enable_sql_reader sets this password. No default, as for Postgres
    sql_reader_password: str = ""
    # Per process, for generated queries only: the other reads of a data question use query_pool_size. A connection is
    # held while one query runs, 5 s at most
    sql_pool_size: int = 5

    # ---- routing ----
    # The model that decides which way a question goes (app/router): chosen by the evaluation's accuracy, latency and
    # cost, like the others
    router_model: str = "gpt-6-luna"
    # Below this confidence both paths run (app/router/routing.py). A path missing loses the answer, one run for nothing
    # costs about $0.0003, so the fewest missing paths decide first. On dev: 3 questions lost a path at any threshold up
    # to 0.9, 2 at 0.95, in both runs; the cost was a third of the questions running both paths instead of 18%. The
    # other two were routed at 0.98 to 0.99, so no threshold saves them (docs/eval-report.md)
    router_min_confidence: float = 0.95
    # Past this the keyword rule decides, so a slow router can't spend the query's budget. Measured on a development
    # machine: p50 1.2 s, p95 1.5 to 1.6 s; 1 and 7 of 108 questions took longer
    router_timeout_seconds: float = 2.0
    # How hard the model reasons before answering from documents and data together (app/query_flow.py): none, low,
    # medium or high. None, as every other answer, until the evaluation grades these answers: planning advice is where
    # reasoning could pay, and it is slower and costs output tokens
    compose_reasoning_effort: str = "none"

    llm_service_host: str = "127.0.0.1"
    llm_service_port: int = 8000
    # Processes of the API. One reaches about 100 QPS, and 4 met the 300 QPS peak
    llm_service_workers: int = 1

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
