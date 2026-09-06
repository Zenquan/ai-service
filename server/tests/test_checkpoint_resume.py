"""LangGraph checkpoint 生产持久化：原生多轮恢复 + MySQL checkpointer 选择。

验证迭代「澄清/转人工中间态重启可恢复」的两层语义：
- 图状态由 checkpointer（而非 agent 实例）持有，同一 saver 跨 agent 实例重建后仍可恢复；
- 无 MySQL 时回退 InMemorySaver，有 MYSQL_HOST 时才走 MySQL（复用 chat_store 的 env 约定）。
"""
from __future__ import annotations

import pytest

from langgraph.checkpoint.memory import InMemorySaver

from server.graph.customer_service import create_customer_service_agent
from server.services.checkpoint_saver import MysqlCheckpointSaver, build_checkpointer


def _retriever(contexts):
    def search(query: str, top_k: int = 5) -> list[dict]:
        return contexts[:top_k]

    return search


def _generator(answer: str):
    def generate(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
        return answer, []

    return generate


def _agent(checkpointer):
    return create_customer_service_agent(
        retriever=_retriever([]),
        generator=_generator("不应调用模型"),
        checkpointer=checkpointer,
    )


def test_agent_resumes_order_clarify_with_shared_checkpointer():
    """模拟重启恢复：状态在 saver 里，重建 agent（共享 saver）后按 thread_id 恢复中间态。"""
    saver = InMemorySaver()

    first = _agent(saver).ask("帮我查一下订单物流", conversation_id="c-resume", user_id="demo-user")
    assert first["response_mode"] == "clarify"
    assert first["needs_clarification"] is True

    # 模拟进程重启：同一持久化 saver 重新构建 agent，继续同一 conversation_id。
    second = _agent(saver).ask("A00001", conversation_id="c-resume", user_id="demo-user")
    assert second["response_mode"] == "answer"
    assert second["needs_human"] is False
    assert "运输中" in second["final_answer"]
    assert "顺丰速运" in second["final_answer"]


def test_order_clarify_does_not_lock_later_intent():
    """澄清中间态不应把后续新问题锁死在订单分支（延续性判定在 classify_intent）。"""
    saver = InMemorySaver()
    agent = _agent(saver)

    first = agent.ask("帮我查订单", conversation_id="c-lock", user_id="demo-user")
    assert first["response_mode"] == "clarify"

    second = agent.ask("AI是什么", conversation_id="c-lock", user_id="demo-user")
    assert second["intent"] == "knowledge_question"


def test_build_checkpointer_without_mysql_returns_inmemory(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    saver = build_checkpointer()
    assert isinstance(saver, InMemorySaver)


def test_mysql_checkpoint_saver_requires_host(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    with pytest.raises(ValueError):
        MysqlCheckpointSaver()


def test_mysql_checkpoint_saver_reads_env_config(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_USER", "alice")
    monkeypatch.setenv("MYSQL_DB", "csdb")
    saver = MysqlCheckpointSaver()
    assert saver._config["host"] == "127.0.0.1"
    assert saver._config["port"] == 3307
    assert saver._config["user"] == "alice"
    assert saver._config["database"] == "csdb"
