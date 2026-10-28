"""调一次真实的 embedding 接口,看看输入输出长什么样。会花一点点钱(几十个 token,远不到 0.0001 美元)。

跑法:
    cd llm-service
    uv run python -m scripts.try_embedding
    uv run python -m scripts.try_embedding "直播间被限流了怎么办" "风控申诉要准备什么材料"

不会打印 API key。
"""
import sys

from app.rag.embeddings import openai_embedder_from_settings

# text-embedding-3-small 的标准价:每 100 万 token 0.02 美元(OpenAI 定价页,2026-10)
PRICE_PER_MILLION_TOKENS = 0.02

DEFAULT_TEXTS = [
    "直播间突然被限流了,应该怎么处理?",
    "风控问题处理流程:先在风控后台确认命中的规则和账号,再填写申诉工单。",
    "今天中午吃什么?",
]


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def main(texts: list[str]) -> None:
    embedder = openai_embedder_from_settings()
    batch = embedder.embed(texts)

    print(f"模型:{embedder.model}\n")
    for i, (text, vector) in enumerate(zip(texts, batch.vectors), start=1):
        print(f"[{i}] {text}")
        print(f"    {len(vector)} 维,前 5 个数:{[round(x, 4) for x in vector[:5]]}")
        # OpenAI 返回的向量长度(模)已经归一化成 1,所以两两点积就等于余弦相似度
        print(f"    向量长度:{dot(vector, vector) ** 0.5:.4f}")

    if len(texts) > 1:
        print("\n两两之间的余弦相似度(越接近 1 意思越像):")
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                print(f"    [{i + 1}] vs [{j + 1}]:{dot(batch.vectors[i], batch.vectors[j]):.4f}")

    cost = batch.total_tokens * PRICE_PER_MILLION_TOKENS / 1_000_000
    print(f"\n本次计费 {batch.total_tokens} 个 token,约 {cost:.8f} 美元")


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT_TEXTS)
