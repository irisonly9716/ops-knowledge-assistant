"""Embedding:把文本变成向量。

调用方(以后的摄入和检索)只依赖 Embedder 这个小接口,不关心背后是 OpenAI 还是别的模型。
测试里换成假的实现,不联网、不花钱——跟消费者把 Kafka 客户端当参数传进来是同一个思路。

这里只做"调一次接口"这一件事。大批量切分、限流这些等摄入链路接进来时再加;
网络错误、429、5xx 的重试 OpenAI SDK 已经自带(max_retries,指数退避)。
"""
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from app.config import settings

# doc_chunks.embedding 是 VECTOR(1536)。维度对不上的向量写不进数据库,在这里就提前拦住,
# 报错信息也比数据库那句 "expected 1536 dimensions" 更清楚是哪一步出的问题
EMBEDDING_DIMENSIONS = 1536


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]  # 每段输入文本一个向量,顺序跟输入一致
    total_tokens: int           # 这次调用计费的 token 数,按它算成本


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> EmbeddingBatch: ...


class OpenAIEmbedder:
    def __init__(self, client: OpenAI, model: str, dimensions: int = EMBEDDING_DIMENSIONS):
        self._client = client
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(vectors=[], total_tokens=0)  # 没东西要算,不发一次没意义的请求
        if any(not text.strip() for text in texts):
            # 接口会直接拒绝空字符串;空文本 embedding 出来也没有意义,说明上游切块出了问题
            raise ValueError("不能对空文本做 embedding")

        response = self._client.embeddings.create(
            model=self.model,
            input=texts,
            # text-embedding-3 系列可以指定输出维度。显式传上,以后换成 3-large 也能输出
            # 1536 维、放进同一列(维度变小,质量会略降,这是它允许的取舍)
            dimensions=self.dimensions,
        )
        # 每条结果带着它对应输入的位置(index),按它排序,不依赖接口返回的顺序
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(texts):
            raise ValueError(f"发了 {len(texts)} 段文本,只收到 {len(vectors)} 个向量")
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"{self.model} 返回了 {len(vector)} 维向量,数据库的 embedding 列是 {self.dimensions} 维")
        return EmbeddingBatch(vectors=vectors, total_tokens=response.usage.total_tokens)


def openai_embedder_from_settings() -> OpenAIEmbedder:
    if not settings.openai_api_key:
        raise RuntimeError("没有配置 OPENAI_API_KEY:在仓库根目录的 .env 里填上(.env 不进 git)")
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=30)
    return OpenAIEmbedder(client, settings.embedding_model)
