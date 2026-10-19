"""向量召回上下文测试（方案1）：注入假 embedding，不打真实 API。"""
import numpy as np
import pytest

from app.config import settings
from app.context_engine import ContextEngine


def make_engine():
    return ContextEngine()


async def fake_embed_keyword(texts):
    """假 embedding：含"虾线"→[1,0]，含"甜点"→[0,1]，其它→[0.5,0.5]"""
    vecs = []
    for t in texts:
        if "虾线" in t:
            vecs.append(np.asarray([1.0, 0.0], dtype=np.float32))
        elif "甜点" in t:
            vecs.append(np.asarray([0.0, 1.0], dtype=np.float32))
        else:
            vecs.append(np.asarray([0.5, 0.5], dtype=np.float32))
    return vecs


async def seed_turns(engine, session_id, count, user_text, assistant_text):
    for i in range(count):
        engine.record_user_message(session_id, f"{user_text}（第{i}条）")
        engine.record_assistant_message(session_id, f"{assistant_text}（第{i}条）")


@pytest.mark.asyncio
async def test_short_session_no_vector_call():
    """短会话（未超压缩窗口）：全量直出，不触发 embedding 调用"""
    engine = make_engine()
    called = []

    async def spy(texts):
        called.append(texts)
        return await fake_embed_keyword(texts)

    engine._embed_override = spy
    await seed_turns(engine, "s1", 3, "问", "答")

    msgs = await engine.build_history_async("s1")
    assert len(called) == 0  # 未触发 embedding
    assert len(msgs) == 6    # 3 轮 × 2 条消息


@pytest.mark.asyncio
async def test_over_window_retrieves_relevant_old_turn(monkeypatch):
    """超窗会话：召回语义相关的早期轮，无关轮被阈值过滤"""
    monkeypatch.setattr(settings, "context_vector_enabled", True)
    engine = make_engine()
    engine._embed_override = fake_embed_keyword

    sid = "s2"
    # 文本加长以越过 2000 token 压缩阈值（中文 ≈1.5 token/字，10 轮 × ~170 字 ≈ 2550）
    filler = "这里有一段补充说明用来撑起上下文长度。" * 8  # ~160 字/轮，10 轮总字符 ×1.5 ≈ 2700 token
    # 早期：1 轮虾线 + 5 轮甜点
    engine.record_user_message(sid, "虾线怎么去掉最干净")
    engine.record_assistant_message(sid, "用牙签从虾背第二节挑出虾线即可。" + filler)
    await seed_turns(engine, sid, 5, "推荐个甜点", "可以试试芒果布丁。" + filler)
    # 近期 4 轮：虾线相关追问
    await seed_turns(engine, sid, 4, "虾线不去行不行", "煮熟了不影响安全。" + filler)

    # 当前待回复的问题（pending，作为查询词）
    engine.record_user_message(sid, "虾线一定要去掉吗")
    msgs = await engine.build_history_async(sid)

    # 结构：召回块(1条system) + 最近4轮(8条) = 9
    assert len(msgs) == 9
    assert msgs[0]["role"] == "system"
    assert "早期相关对话回忆" in msgs[0]["content"]
    # 召回的是虾线早期轮，不是甜点轮
    assert "虾线怎么去掉" in msgs[0]["content"]
    assert "甜点" not in msgs[0]["content"]
    # 最近 4 轮原文在
    joined = "\n".join(m["content"] for m in msgs[1:])
    assert "煮熟了不影响安全" in joined


@pytest.mark.asyncio
async def test_embed_failure_falls_back_to_heuristic(monkeypatch):
    """embedding 失败：自动回落启发式压缩，不抛异常"""
    monkeypatch.setattr(settings, "context_vector_enabled", True)
    engine = make_engine()

    async def broken(texts):
        return None

    engine._embed_override = broken
    await seed_turns(engine, "s3", 12, "问问题", "回答内容，用来撑大 token 量。" * 10)

    msgs = await engine.build_history_async("s3")
    assert len(msgs) > 0
    assert engine.vector_degraded is True
    # 回落后应是启发式压缩的结构（含摘要 system 消息或全量轮次），无召回块
    assert all("早期相关对话回忆" not in m["content"] for m in msgs)


@pytest.mark.asyncio
async def test_disabled_uses_heuristic(monkeypatch):
    """开关关闭：完全走原同步逻辑，不触发 embedding"""
    monkeypatch.setattr(settings, "context_vector_enabled", False)
    engine = make_engine()
    called = []

    async def spy(texts):
        called.append(texts)
        return await fake_embed_keyword(texts)

    engine._embed_override = spy
    await seed_turns(engine, "s4", 12, "问", "答" * 100)

    msgs = await engine.build_history_async("s4")
    assert len(called) == 0
    assert len(msgs) > 0


@pytest.mark.asyncio
async def test_warm_index_persists_embeddings(monkeypatch):
    """预热：批量补全轮次向量，后续 build 不再调用 embedding"""
    monkeypatch.setattr(settings, "context_vector_enabled", True)
    engine = make_engine()
    calls = []

    async def counting_embed(texts):
        calls.append(len(texts))
        return await fake_embed_keyword(texts)

    engine._embed_override = counting_embed
    await seed_turns(engine, "s5", 8, "问", "答")
    assert engine._get_history_manager("s5").get_turn_count() == 8

    await engine.warm_vector_index("s5")
    assert calls == [8]  # 一次批量补全 8 轮

    # 再次预热：全部有向量，不再调用
    await engine.warm_vector_index("s5")
    assert calls == [8]
