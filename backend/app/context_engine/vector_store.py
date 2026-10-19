"""向量检索存储 —— 超窗历史轮次的语义召回（方案1：embedding 召回替代启发式摘要）。

- embedding：SiliconFlow `BAAI/bge-m3`（复用现有 SILICONFLOW_API_KEY），批量接口
- 存储：内存 numpy（每会话 ≤100 轮，暴力余弦微秒级，无需独立向量库/持久化——
  轮次本体已在 HistoryManager 内存里，向量只挂在 ChatTurn.embedding 上随生随灭，
  断线重连由 _restore_context_engine 回灌轮次后批量重建）
- 失败语义：任何异常返回 None，调用方回落启发式压缩，绝不阻塞/炸主链路
"""
from typing import List, Optional, Tuple

import httpx
import numpy as np

from ..logger import backend_log

_EMBED_TIMEOUT = 8.0


async def embed_texts(
    texts: List[str], api_key: str, model: str, base_url: str
) -> Optional[List[np.ndarray]]:
    """批量 embedding；成功返回与 texts 等长的向量列表，任何失败返回 None。"""
    if not texts:
        return []
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": texts}
    try:
        async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT, trust_env=False) as client:
            resp = await client.post(base_url, headers=headers, json=payload)
        if resp.status_code != 200:
            backend_log(f"[向量记忆] embedding HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json().get("data") or []
        if len(data) != len(texts):
            backend_log(f"[向量记忆] embedding 返回数量不匹配 {len(data)}/{len(texts)}")
            return None
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [np.asarray(item["embedding"], dtype=np.float32) for item in ordered]
    except Exception as exc:
        backend_log(f"[向量记忆] embedding 失败: {exc}")
        return None


def cosine_topk(
    query: np.ndarray,
    items: List[Tuple[int, np.ndarray]],
    k: int,
    min_similarity: float,
) -> List[Tuple[int, float]]:
    """从候选 (索引, 向量) 中取与 query 余弦相似度最高的 k 个，低于阈值丢弃。

    返回 [(索引, 相似度)]，按相似度降序。
    """
    if not items or query is None:
        return []
    q = np.asarray(query, dtype=np.float32)
    q_norm = float(np.linalg.norm(q))
    if q_norm == 0:
        return []
    sims: List[Tuple[int, float]] = []
    for idx, vec in items:
        v = np.asarray(vec, dtype=np.float32)
        denom = q_norm * float(np.linalg.norm(v))
        if denom == 0:
            continue
        sims.append((idx, float(np.dot(q, v) / denom)))
    sims.sort(key=lambda x: x[1], reverse=True)
    return [(i, s) for i, s in sims[:k] if s >= min_similarity]
